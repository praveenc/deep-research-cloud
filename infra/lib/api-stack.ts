import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigatewayv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { DeepResearchConfig } from '../config';
import * as path from 'path';

export interface ApiStackProps extends cdk.StackProps {
  readonly config: DeepResearchConfig;
  readonly connectionsTable: dynamodb.ITable;
  readonly trackingTable: dynamodb.ITable;
  readonly researchBucket: cdk.aws_s3.IBucket;
}

/**
 * API Layer — REST API + WebSocket API + Cognito User Pool.
 *
 * REST:
 *   POST /research              → Pre-processing Lambda (intent / strategy
 *                                  / decompose / research contract / slug)
 *   GET  /research/{slug}/status → poll task status from DDB
 *
 * The Agent Lambda that performs the actual research is wired in a follow-up
 * PR; until then AGENT_LAMBDA_NAME is empty and the pre-processor returns
 * the plan to the client without dispatching anything downstream.
 *
 * WebSocket: Real-time progress updates from agent → client
 */
export class ApiStack extends cdk.Stack {
  public readonly webSocketApi: apigatewayv2.WebSocketApi;
  public readonly webSocketStage: apigatewayv2.WebSocketStage;
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    // ─── Cognito User Pool ───────────────────────────────────────────
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `deep-research-${props.config.stage}`,
      selfSignUpEnabled: false, // Admin-created users only
      signInAliases: { email: true },
      passwordPolicy: {
        minLength: 12,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.userPoolClient = this.userPool.addClient('WebClient', {
      userPoolClientName: `deep-research-web-${props.config.stage}`,
      authFlows: {
        userSrp: true,
      },
      generateSecret: false,
      preventUserExistenceErrors: true,
    });

    // ─── REST API — Pre-processing + Status ─────────────────────────────────
    // Pre-processing Lambda — implements Steps 1, 2, 3, 1g of the local
    // aws-deep-research skill (intent / strategy / decompose / contract /
    // slug). One Strands `agent.structured_output` call drives the plan;
    // contract is written to S3, tracking record to DDB, and (when an
    // Agent Lambda exists in a follow-up PR) simple queries auto-dispatch.
    const preprocessHandler = new lambda.Function(this, 'PreprocessHandler', {
      functionName: `deep-research-preprocess-${props.config.stage}`,
      description: 'Steps 1–3 + 1g of the aws-deep-research skill: classify intent, build research contract, decompose, mint slug',
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/preprocess')),
      timeout: cdk.Duration.seconds(90),  // 1–2 LLM calls, ~15–30s typical
      memorySize: 512,
      environment: {
        STAGE: props.config.stage,
        TRACKING_TABLE: props.trackingTable.tableName,
        RESEARCH_BUCKET: props.researchBucket.bucketName,
        BEDROCK_MODEL_ID: props.config.bedrockModelId,
        // Wired post-deploy when the Agent Lambda lands (follow-up PR).
        // Empty string disables the auto-dispatch hand-off; pre-processor
        // still returns the full plan to the client.
        AGENT_LAMBDA_NAME: '',
      },
    });

    // Pre-processor permissions — strict least privilege
    props.trackingTable.grantWriteData(preprocessHandler);
    props.researchBucket.grantPut(preprocessHandler);  // contract.md + plan.json under <slug>/

    // Bedrock model invocation — scope to the configured inference profile
    // and the Anthropic foundation models it forwards to. Cross-region
    // inference profiles span regions; wildcard the region in the ARN.
    preprocessHandler.addToRolePolicy(new iam.PolicyStatement({
      sid: 'InvokeBedrockModel',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: [
        `arn:aws:bedrock:*:${this.account}:inference-profile/${props.config.bedrockModelId}`,
        `arn:aws:bedrock:*::foundation-model/anthropic.claude-*`,
      ],
    }));

    // Status handler — reads task status from DynamoDB
    const statusHandler = new lambda.Function(this, 'StatusHandler', {
      functionName: `deep-research-status-${props.config.stage}`,
      description: 'Returns research task status and progress from DynamoDB',
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/agent/status')),
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: {
        TRACKING_TABLE: props.trackingTable.tableName,
        RESEARCH_BUCKET: props.researchBucket.bucketName,
      },
    });
    props.trackingTable.grantReadData(statusHandler);
    props.researchBucket.grantRead(statusHandler);

    // REST API with Cognito authorizer
    const restApi = new apigateway.RestApi(this, 'RestApi', {
      restApiName: `deep-research-api-${props.config.stage}`,
      description: 'Deep Research Cloud — REST API for research invocation and status',
      deployOptions: {
        stageName: props.config.stage,
        throttlingRateLimit: 10,
        throttlingBurstLimit: 20,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: ['GET', 'POST', 'OPTIONS'],
        allowHeaders: ['Content-Type', 'Authorization'],
      },
    });

    const cognitoAuthorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'CognitoAuth', {
      cognitoUserPools: [this.userPool],
      identitySource: 'method.request.header.Authorization',
    });

    // POST /research — invokes the Pre-processing Lambda. For complex
    // queries the response carries the contract for client-side approval;
    // for simple queries the Lambda hand-offs to the Agent Lambda async
    // (no-op until AGENT_LAMBDA_NAME is set in a follow-up PR).
    const researchResource = restApi.root.addResource('research');
    researchResource.addMethod('POST',
      new apigateway.LambdaIntegration(preprocessHandler),
      {
        authorizer: cognitoAuthorizer,
        authorizationType: apigateway.AuthorizationType.COGNITO,
      }
    );

    // GET /research/{slug}/status — poll status
    const slugResource = researchResource.addResource('{slug}');
    const statusResource = slugResource.addResource('status');
    statusResource.addMethod('GET',
      new apigateway.LambdaIntegration(statusHandler),
      {
        authorizer: cognitoAuthorizer,
        authorizationType: apigateway.AuthorizationType.COGNITO,
      }
    );

    // ─── WebSocket API ───────────────────────────────────────────────
    // $connect: authenticate + store connectionId in DDB
    // $disconnect: remove connectionId from DDB

    const connectHandler = new lambda.Function(this, 'WsConnectHandler', {
      functionName: `deep-research-ws-connect-${props.config.stage}`,
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.connect',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/agent/ws-handlers')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        CONNECTIONS_TABLE: props.connectionsTable.tableName,
        USER_POOL_ID: this.userPool.userPoolId,
        USER_POOL_CLIENT_ID: this.userPoolClient.userPoolClientId,
      },
    });
    props.connectionsTable.grantWriteData(connectHandler);

    const disconnectHandler = new lambda.Function(this, 'WsDisconnectHandler', {
      functionName: `deep-research-ws-disconnect-${props.config.stage}`,
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.disconnect',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/agent/ws-handlers')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        CONNECTIONS_TABLE: props.connectionsTable.tableName,
      },
    });
    props.connectionsTable.grantWriteData(disconnectHandler);

    this.webSocketApi = new apigatewayv2.WebSocketApi(this, 'WebSocketApi', {
      apiName: `deep-research-ws-${props.config.stage}`,
      connectRouteOptions: {
        integration: new apigatewayv2Integrations.WebSocketLambdaIntegration(
          'ConnectIntegration', connectHandler
        ),
      },
      disconnectRouteOptions: {
        integration: new apigatewayv2Integrations.WebSocketLambdaIntegration(
          'DisconnectIntegration', disconnectHandler
        ),
      },
    });

    this.webSocketStage = new apigatewayv2.WebSocketStage(this, 'WsStage', {
      webSocketApi: this.webSocketApi,
      stageName: props.config.stage,
      autoDeploy: true,
    });

    // ─── Outputs ─────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'UserPoolId', {
      value: this.userPool.userPoolId,
      exportName: `${props.config.stage}-user-pool-id`,
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: this.userPoolClient.userPoolClientId,
      exportName: `${props.config.stage}-user-pool-client-id`,
    });

    new cdk.CfnOutput(this, 'RestApiUrl', {
      value: restApi.url,
      exportName: `${props.config.stage}-rest-api-url`,
    });

    new cdk.CfnOutput(this, 'WebSocketUrl', {
      value: this.webSocketStage.url,
      exportName: `${props.config.stage}-ws-url`,
    });
  }
}

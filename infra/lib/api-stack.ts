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
 *   POST /research       → async invoke AgentCore Runtime agent
 *   GET  /research/{slug}/status → poll task status from DDB
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

    // ─── REST API — Invoker + Status ─────────────────────────────────
    // Thin Lambda that validates the request and async-invokes AgentCore Runtime
    const invokerHandler = new lambda.Function(this, 'InvokerHandler', {
      functionName: `deep-research-invoker-${props.config.stage}`,
      description: 'Validates research request, writes task to DDB, async-invokes AgentCore Runtime',
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/agent/invoker')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      environment: {
        STAGE: props.config.stage,
        TRACKING_TABLE: props.trackingTable.tableName,
        RESEARCH_BUCKET: props.researchBucket.bucketName,
        // AGENT_RUNTIME_ID is set post-deploy via SSM or env update
        AGENT_RUNTIME_ID: '', // Populated after AgentRuntimeStack deploys
      },
    });

    // Invoker permissions
    props.trackingTable.grantWriteData(invokerHandler);
    invokerHandler.addToRolePolicy(new iam.PolicyStatement({
      sid: 'InvokeAgentRuntime',
      effect: iam.Effect.ALLOW,
      actions: ['bedrock-agentcore:InvokeRuntime'],
      resources: ['*'], // Scoped after runtime ARN is known
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

    // POST /research — async invoke
    const researchResource = restApi.root.addResource('research');
    researchResource.addMethod('POST',
      new apigateway.LambdaIntegration(invokerHandler),
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

import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';
import { Construct } from 'constructs';
import { DeepResearchConfig } from '../config';
import * as path from 'path';

export interface AgentRuntimeStackProps extends cdk.StackProps {
  readonly config: DeepResearchConfig;
  readonly researchBucket: s3.IBucket;
  readonly trackingTable: dynamodb.ITable;
  readonly connectionsTable: dynamodb.ITable;
  readonly mcpFunctions: Record<string, lambda.IFunction>;
  readonly userPool: cognito.IUserPool;
  readonly userPoolClient: cognito.IUserPoolClient;
  readonly webSocketApi: apigatewayv2.WebSocketApi;
  readonly webSocketStage: apigatewayv2.WebSocketStage;
  readonly secretArn: string;
}

/**
 * AgentCore Runtime — Single Strands Agent running the deep research orchestrator.
 *
 * Uses @aws-cdk/aws-bedrock-agentcore-alpha to deploy a containerized agent
 * with Cognito authentication, Bedrock model access, and permissions to
 * invoke MCP Lambda servers + push WebSocket progress.
 *
 * The agent container includes:
 * - strands-agents SDK with OTel instrumentation
 * - AgentSkills plugin for progressive skill disclosure
 * - Pattern 3 (Meta-Tool) sub-agents for context isolation
 */
export class AgentRuntimeStack extends cdk.Stack {
  public readonly runtime: agentcore.Runtime;

  constructor(scope: Construct, id: string, props: AgentRuntimeStackProps) {
    super(scope, id, props);

    // ─── Execution Role ──────────────────────────────────────────────
    // Explicit role for fine-grained permissions (least privilege)
    const executionRole = new iam.Role(this, 'AgentExecutionRole', {
      roleName: `deep-research-agent-${props.config.stage}`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role for Deep Research AgentCore Runtime agent',
    });

    // Bedrock model invocation (Converse API)
    executionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'BedrockModelInvocation',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/${props.config.bedrockModelId}`,
        // Allow cross-region inference profiles if needed
        `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
      ],
    }));

    // S3: Read/write research artifacts
    props.researchBucket.grantReadWrite(executionRole);

    // DynamoDB: Task tracking + cost ledger
    props.trackingTable.grantReadWriteData(executionRole);

    // DynamoDB: Read connections for WS push
    props.connectionsTable.grantReadData(executionRole);

    // Lambda: Invoke MCP servers (Direct Invoke via IAM)
    Object.values(props.mcpFunctions).forEach(fn => {
      fn.grantInvoke(executionRole);
    });

    // Secrets Manager: Read API keys for MCP servers
    executionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'SecretsManagerRead',
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue'],
      resources: [props.secretArn],
    }));

    // API Gateway: Post to WebSocket connections (progress push)
    executionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'WebSocketPost',
      effect: iam.Effect.ALLOW,
      actions: ['execute-api:ManageConnections'],
      resources: [
        `arn:aws:execute-api:${this.region}:${this.account}:${props.webSocketApi.apiId}/${props.config.stage}/POST/@connections/*`,
      ],
    }));

    // CloudWatch: Emit custom metrics (token usage, cost)
    executionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchMetrics',
      effect: iam.Effect.ALLOW,
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: {
        StringEquals: {
          'cloudwatch:namespace': 'DeepResearch',
        },
      },
    }));

    // ─── AgentCore Runtime ───────────────────────────────────────────
    // Deploy the agent container from the local Dockerfile
    const agentRuntimeArtifact = agentcore.AgentRuntimeArtifact.fromAsset(
      path.join(__dirname, '../../app/agent/runtime')
    );

    this.runtime = new agentcore.Runtime(this, 'DeepResearchRuntime', {
      runtimeName: `deepResearch_${props.config.stage}`,
      agentRuntimeArtifact: agentRuntimeArtifact,
      executionRole,
      description: 'Deep Research Cloud — single Strands agent orchestrating research with sub-agents and Lambda MCP servers',
      authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingIAM(),
      networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      environmentVariables: {
        STAGE: props.config.stage,
        RESEARCH_BUCKET: props.researchBucket.bucketName,
        TRACKING_TABLE: props.trackingTable.tableName,
        CONNECTIONS_TABLE: props.connectionsTable.tableName,
        SECRET_ARN: props.secretArn,
        BEDROCK_MODEL_ID: props.config.bedrockModelId,
        WS_API_ENDPOINT: `https://${props.webSocketApi.apiId}.execute-api.${this.region}.amazonaws.com/${props.config.stage}`,
        // MCP Lambda ARNs — agent resolves these at runtime
        MCP_FETCH_ARN: props.mcpFunctions['fetch-mcp'].functionArn,
        MCP_AWS_DOCS_ARN: props.mcpFunctions['aws-docs-mcp'].functionArn,
        MCP_BRAVE_ARN: props.mcpFunctions['brave-mcp'].functionArn,
        MCP_GITHUB_ARN: props.mcpFunctions['github-mcp'].functionArn,
        MCP_FEEDS_ARN: props.mcpFunctions['feeds-mcp'].functionArn,
        // OTel configuration
        OTEL_SERVICE_NAME: 'deep-research-agent',
        OTEL_EXPORTER_OTLP_PROTOCOL: 'http/protobuf',
      },
    });

    // Add a production endpoint
    this.runtime.addEndpoint(`deepResearch_${props.config.stage}_endpoint`, {
      description: `Deep Research ${props.config.stage} endpoint`,
    });

    // ─── Outputs ─────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: this.runtime.agentRuntimeArn,
      exportName: `${props.config.stage}-agent-runtime-arn`,
    });

    new cdk.CfnOutput(this, 'AgentRuntimeId', {
      value: this.runtime.agentRuntimeId,
      exportName: `${props.config.stage}-agent-runtime-id`,
    });
  }
}

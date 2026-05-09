import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { DeepResearchConfig } from '../config';
import * as path from 'path';

export interface McpServersStackProps extends cdk.StackProps {
  readonly config: DeepResearchConfig;
  readonly researchBucket: s3.IBucket;
  readonly trackingTable: dynamodb.ITable;
  readonly secretArn: string;
}

/**
 * Lambda MCP Servers — stateless data-fetching tools invoked by the
 * AgentCore Runtime agent via Direct Invoke (IAM).
 *
 * Each Lambda has its own minimal IAM role:
 * - Only secrets it needs (not all secrets)
 * - ADOT Lambda layer for auto-instrumentation
 * - URL blocklist for SSRF prevention (fetch-mcp)
 */
export class McpServersStack extends cdk.Stack {
  public readonly functions: Record<string, lambda.IFunction>;

  constructor(scope: Construct, id: string, props: McpServersStackProps) {
    super(scope, id, props);

    const secret = secretsmanager.Secret.fromSecretNameV2(this, 'SearchSecret', 'prod/deepresearch/Search');

    // ADOT Lambda Layer for OpenTelemetry auto-instrumentation
    // ARM64 layer matching the Lambda architecture
    const adotLayer = lambda.LayerVersion.fromLayerVersionArn(
      this, 'AdotLayer',
      `arn:aws:lambda:${this.region}:901920570463:layer:aws-otel-python-arm64-ver-1-25-0:1`
    );

    // Common Lambda props shared across all MCP server functions
    const commonEnv = {
      AWS_LAMBDA_EXEC_WRAPPER: '/opt/otel-instrument', // ADOT auto-instrumentation
      OTEL_SERVICE_NAME: 'deep-research-mcp',
      OTEL_EXPORTER_OTLP_PROTOCOL: 'http/protobuf',
      POWERTOOLS_LOG_LEVEL: 'INFO',
    };

    const commonProps = {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      layers: [adotLayer],
      tracing: lambda.Tracing.ACTIVE,
    };

    // ─── fetch-mcp ───────────────────────────────────────────────────
    // Fetches arbitrary web content. SSRF protection via URL blocklist.
    const fetchMcp = new lambda.Function(this, 'FetchMcp', {
      ...commonProps,
      functionName: `deep-research-fetch-mcp-${props.config.stage}`,
      description: 'MCP Server: Web content extraction with SSRF protection',
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/mcp-servers/fetch-mcp')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...commonEnv,
        OTEL_SERVICE_NAME: 'fetch-mcp',
      },
    });

    // ─── aws-docs-mcp ────────────────────────────────────────────────
    // Searches AWS documentation using AWS APIs
    const awsDocsMcp = new lambda.Function(this, 'AwsDocsMcp', {
      ...commonProps,
      functionName: `deep-research-aws-docs-mcp-${props.config.stage}`,
      description: 'MCP Server: AWS documentation and pricing search',
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/mcp-servers/aws-docs-mcp')),
      timeout: cdk.Duration.seconds(45),
      environment: {
        ...commonEnv,
        OTEL_SERVICE_NAME: 'aws-docs-mcp',
      },
    });

    // ─── brave-mcp ───────────────────────────────────────────────────
    // Web search via Brave Search API (needs BRAVE_API_KEY)
    const braveMcp = new lambda.Function(this, 'BraveMcp', {
      ...commonProps,
      functionName: `deep-research-brave-mcp-${props.config.stage}`,
      description: 'MCP Server: Brave web search',
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/mcp-servers/brave-mcp')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...commonEnv,
        OTEL_SERVICE_NAME: 'brave-mcp',
        SECRET_ARN: props.secretArn,
        SECRET_KEY_NAME: 'BRAVE_SEARCH_API_KEY',
      },
    });
    // Grant only this Lambda access to the secret
    secret.grantRead(braveMcp);

    // ─── github-mcp ──────────────────────────────────────────────────
    // GitHub repository and code search (needs GITHUB_TOKEN)
    const githubMcp = new lambda.Function(this, 'GithubMcp', {
      ...commonProps,
      functionName: `deep-research-github-mcp-${props.config.stage}`,
      description: 'MCP Server: GitHub repository search',
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/mcp-servers/github-mcp')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...commonEnv,
        OTEL_SERVICE_NAME: 'github-mcp',
        SECRET_ARN: props.secretArn,
        SECRET_KEY_NAME: 'GITHUB_TOKEN',
      },
    });
    secret.grantRead(githubMcp);

    // ─── feeds-mcp ───────────────────────────────────────────────────
    // AWS blog feeds and RSS extraction
    const feedsMcp = new lambda.Function(this, 'FeedsMcp', {
      ...commonProps,
      functionName: `deep-research-feeds-mcp-${props.config.stage}`,
      description: 'MCP Server: Blog/RSS feed extraction',
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../app/mcp-servers/feeds-mcp')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...commonEnv,
        OTEL_SERVICE_NAME: 'feeds-mcp',
      },
    });

    // Reserved concurrency as cost guardrail (per arch review)
    // Limits concurrent executions per MCP server to prevent runaway costs
    const concurrencyLimit = props.config.stage === 'prod' ? 25 : 10;
    [fetchMcp, awsDocsMcp, braveMcp, githubMcp, feedsMcp].forEach(fn => {
      (fn.node.defaultChild as lambda.CfnFunction).addPropertyOverride(
        'ReservedConcurrentExecutions', concurrencyLimit
      );
    });

    // Export function references for the agent to invoke
    this.functions = {
      'fetch-mcp': fetchMcp,
      'aws-docs-mcp': awsDocsMcp,
      'brave-mcp': braveMcp,
      'github-mcp': githubMcp,
      'feeds-mcp': feedsMcp,
    };

    // Outputs
    Object.entries(this.functions).forEach(([name, fn]) => {
      new cdk.CfnOutput(this, `${name}-arn`, {
        value: fn.functionArn,
        exportName: `${props.config.stage}-${name}-arn`,
      });
    });
  }
}

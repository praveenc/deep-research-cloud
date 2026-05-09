/**
 * Environment configuration for Deep Research Cloud.
 * Secrets are already provisioned in the account — we reference them by ARN.
 */
export interface DeepResearchConfig {
  readonly env: {
    readonly account: string;
    readonly region: string;
  };
  /** Secrets Manager ARN for search API keys (Brave, Tavily, GitHub) */
  readonly secretArn: string;
  /** Optional custom domain for CloudFront */
  readonly domainName?: string;
  /** Deployment stage */
  readonly stage: 'dev' | 'staging' | 'prod';
  /** Bedrock model ID for the agent */
  readonly bedrockModelId: string;
  /** Budget alarm threshold in USD */
  readonly monthlyBudgetAlarmUsd: number;
  /** SNS email for alarm notifications */
  readonly alarmEmail?: string;
}

const account = process.env.CDK_DEFAULT_ACCOUNT || '';
const region = 'us-west-2';

export const devConfig: DeepResearchConfig = {
  env: { account, region },
  secretArn: `arn:aws:secretsmanager:${region}:${account}:secret:prod/deepresearch/Search`,
  stage: 'dev',
  bedrockModelId: 'us.anthropic.claude-opus-4-6-v1',
  monthlyBudgetAlarmUsd: 50,
  alarmEmail: undefined, // Set to receive alarm notifications
};

export const prodConfig: DeepResearchConfig = {
  env: { account, region },
  secretArn: `arn:aws:secretsmanager:${region}:${account}:secret:prod/deepresearch/Search`,
  stage: 'prod',
  bedrockModelId: 'us.anthropic.claude-opus-4-6-v1',
  monthlyBudgetAlarmUsd: 200,
  alarmEmail: undefined,
};

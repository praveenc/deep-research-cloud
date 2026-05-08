import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import { Construct } from 'constructs';
import { DeepResearchConfig } from '../config';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly config: DeepResearchConfig;
  readonly mcpFunctions: Record<string, lambda.IFunction>;
}

/**
 * Observability — CloudWatch Dashboard, Budget Alarms, and SNS notifications.
 *
 * Tracing is handled by:
 * - Agent: strands-agents[otel] → OTLP endpoint (configured in Runtime container)
 * - Lambdas: ADOT Lambda Layer (auto-instrumentation, configured in MCP stack)
 *
 * This stack creates the dashboard and alarms that consume those metrics.
 */
export class ObservabilityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);

    // SNS topic for budget/operational alarms
    const alarmTopic = new sns.Topic(this, 'AlarmTopic', {
      topicName: `deep-research-alarms-${props.config.stage}`,
      displayName: 'Deep Research Alarms',
    });

    // ─── CloudWatch Dashboard ────────────────────────────────────────
    const dashboard = new cloudwatch.Dashboard(this, 'Dashboard', {
      dashboardName: `DeepResearch-${props.config.stage}`,
      periodOverride: cloudwatch.PeriodOverride.AUTO,
    });

    // Bedrock Token Metrics
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Bedrock Token Usage',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'InputTokenCount',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'Sum',
            period: cdk.Duration.hours(1),
          }),
          new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'OutputTokenCount',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'Sum',
            period: cdk.Duration.hours(1),
          }),
        ],
        right: [
          new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'CacheReadInputTokens',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'Sum',
            period: cdk.Duration.hours(1),
          }),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Bedrock Latency & TTFT',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'InvocationLatency',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'p99',
            period: cdk.Duration.minutes(5),
          }),
        ],
        right: [
          new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'TimeToFirstToken',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'p50',
            period: cdk.Duration.minutes(5),
          }),
        ],
        width: 12,
      }),
    );

    // MCP Server Lambda Metrics
    const mcpWidgets = Object.entries(props.mcpFunctions).map(([name, fn]) => {
      return new cloudwatch.GraphWidget({
        title: `${name}`,
        left: [fn.metricDuration({ statistic: 'p99' })],
        right: [fn.metricErrors()],
        width: 4,
      });
    });
    dashboard.addWidgets(...mcpWidgets);

    // ─── Budget Alarms ───────────────────────────────────────────────

    // Alarm: High token usage (cost spike per hour)
    const tokenAlarm = new cloudwatch.Alarm(this, 'HighTokenUsage', {
      alarmName: `DeepResearch-HighTokens-${props.config.stage}`,
      alarmDescription: 'Token usage exceeds 500K in 1 hour — potential cost spike',
      metric: new cloudwatch.MathExpression({
        expression: 'input + output',
        usingMetrics: {
          input: new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'InputTokenCount',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'Sum',
            period: cdk.Duration.hours(1),
          }),
          output: new cloudwatch.Metric({
            namespace: 'AWS/Bedrock',
            metricName: 'OutputTokenCount',
            dimensionsMap: { ModelId: 'anthropic.claude-sonnet-4-20250514' },
            statistic: 'Sum',
            period: cdk.Duration.hours(1),
          }),
        },
        period: cdk.Duration.hours(1),
      }),
      threshold: 500000,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    });
    tokenAlarm.addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    // Alarm: MCP server errors
    Object.entries(props.mcpFunctions).forEach(([name, fn]) => {
      const errorAlarm = new cloudwatch.Alarm(this, `${name}-errors`, {
        alarmName: `DeepResearch-${name}-errors-${props.config.stage}`,
        metric: fn.metricErrors({ period: cdk.Duration.minutes(5) }),
        threshold: 3,
        evaluationPeriods: 1,
      });
      errorAlarm.addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));
    });

    // Outputs
    new cdk.CfnOutput(this, 'DashboardUrl', {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#dashboards:name=${dashboard.dashboardName}`,
    });

    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: alarmTopic.topicArn,
      exportName: `${props.config.stage}-alarm-topic-arn`,
    });
  }
}

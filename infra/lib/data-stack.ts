import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { DeepResearchConfig } from '../config';

export interface DataStackProps extends cdk.StackProps {
  readonly config: DeepResearchConfig;
}

/**
 * Stateful resources — S3 bucket for research artifacts, DynamoDB tables
 * for task tracking, cost ledger, and WebSocket connection management.
 *
 * Separated from compute stacks to protect against accidental deletion
 * during frequent deployments.
 */
export class DataStack extends cdk.Stack {
  public readonly researchBucket: s3.IBucket;
  public readonly trackingTable: dynamodb.Table;
  public readonly connectionsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    // S3 Bucket: research artifacts (contract, findings, report, visuals)
    this.researchBucket = new s3.Bucket(this, 'ResearchBucket', {
      bucketName: `deep-research-${props.config.stage}-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          id: 'ArchiveOldResearch',
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: cdk.Duration.days(90),
            },
            {
              storageClass: s3.StorageClass.GLACIER,
              transitionAfter: cdk.Duration.days(365),
            },
          ],
        },
      ],
      versioned: true,
    });

    // DynamoDB: Task tracking + cost ledger
    this.trackingTable = new dynamodb.Table(this, 'TrackingTable', {
      tableName: `deep-research-tracking-${props.config.stage}`,
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING }, // userId#slug
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },      // step | cost | meta
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecovery: true,
      timeToLiveAttribute: 'ttl',
    });

    // GSI for querying by status (active research runs)
    this.trackingTable.addGlobalSecondaryIndex({
      indexName: 'status-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'createdAt', type: dynamodb.AttributeType.STRING },
    });

    // DynamoDB: WebSocket connection management
    this.connectionsTable = new dynamodb.Table(this, 'ConnectionsTable', {
      tableName: `deep-research-connections-${props.config.stage}`,
      partitionKey: { name: 'connectionId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY, // connections are ephemeral
      timeToLiveAttribute: 'ttl',
    });

    // GSI for looking up connections by userId (for WS push)
    this.connectionsTable.addGlobalSecondaryIndex({
      indexName: 'user-index',
      partitionKey: { name: 'userId', type: dynamodb.AttributeType.STRING },
    });

    // Outputs
    new cdk.CfnOutput(this, 'ResearchBucketName', {
      value: this.researchBucket.bucketName,
      exportName: `${props.config.stage}-research-bucket`,
    });
  }
}

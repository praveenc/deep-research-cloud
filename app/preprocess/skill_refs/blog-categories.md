# AWS Blog Feed Categories

Maps AWS services and topics to their corresponding blog RSS feed URLs.
Used by the skill to determine which blog feeds to search based on query content.

## Feed URLs

| Category | Feed URL |
|---|---|
| whatsnew | `https://aws.amazon.com/about-aws/whats-new/recent/feed/` |
| machinelearning | `https://aws.amazon.com/blogs/machine-learning/feed/` |
| security | `https://aws.amazon.com/blogs/security/feed/` |
| bigdata | `https://aws.amazon.com/blogs/big-data/feed/` |
| databases | `https://aws.amazon.com/blogs/database/feed/` |
| containers | `https://aws.amazon.com/blogs/containers/feed/` |
| serverless | `https://aws.amazon.com/blogs/compute/tag/serverless/feed/` |
| operations | `https://aws.amazon.com/blogs/mt/feed/` |
| opensource | `https://aws.amazon.com/blogs/opensource/feed/` |

## Service-to-Category Mapping

### whatsnew
All AWS service launches, feature releases, region expansions, and pricing
changes. The What's New feed contains the 100 most recent announcements across
all services. **Always include this feed** for `news-updates` intent or when
researching features launched in the last 30 days.

### machinelearning
Amazon Bedrock, Amazon SageMaker AI, Amazon Bedrock AgentCore (Runtime, Memory,
Gateway, Code Interpreter, Browser, Observability, Identity), foundation models,
Amazon Titan, Amazon Nova, ML training, inference, RAG, agents, generative AI,
computer vision, NLP, MLOps, Amazon Rekognition, Amazon Comprehend, Amazon
Textract, Amazon Transcribe, Amazon Polly, Amazon Personalize, Amazon Forecast,
Amazon Kendra, Amazon Q, Amazon Lex

### security
IAM, AWS KMS, AWS Secrets Manager, Amazon GuardDuty, AWS Security Hub, AWS WAF,
AWS Shield, AWS CloudTrail, Amazon Macie, AWS Config (security rules), encryption,
compliance, identity federation, SSO, access control, zero trust, VPC security,
security groups, NACLs, AWS Firewall Manager, Amazon Inspector

### bigdata
Amazon EMR, Amazon Athena, Amazon Redshift, AWS Glue, AWS Lake Formation,
Amazon Kinesis, Amazon OpenSearch Service, Amazon MSK, AWS Data Pipeline,
Amazon QuickSight, data lakes, ETL, streaming analytics, data warehousing,
Apache Spark on AWS, Apache Kafka on AWS, search analytics, vector search
(when using OpenSearch)

### databases
Amazon RDS, Amazon Aurora, Amazon DynamoDB, Amazon ElastiCache, Amazon Neptune,
Amazon DocumentDB, Amazon Timestream, Amazon MemoryDB, Amazon Keyspaces, AWS DMS,
database migration, database optimization, read replicas, multi-AZ

### containers
Amazon ECS, Amazon EKS, AWS Fargate, Amazon ECR, AWS App Runner, Docker on AWS,
Kubernetes on AWS, container orchestration, service mesh, AWS App Mesh, AWS
Proton (container workloads)

### serverless
AWS Lambda, AWS Step Functions, Amazon API Gateway, Amazon EventBridge, Amazon
SQS, Amazon SNS, AWS AppSync, serverless architectures, event-driven design,
serverless application model (SAM)

### operations
AWS CloudFormation, AWS CDK, AWS Systems Manager, Amazon CloudWatch, AWS Config,
AWS Organizations, AWS Control Tower, AWS Service Catalog, AWS Trusted Advisor,
infrastructure as code, management and governance, monitoring, observability,
cost management, AWS Budgets

### opensource
Open source on AWS, OSIS, OpenSearch, Linux, Kubernetes, Terraform on AWS,
Bottlerocket, Firecracker, Cedar, open source contributions, CNCF projects

## Common Miscategorizations

These services are frequently assigned to the wrong blog category. Always
check this list before categorizing.

| Service | Correct Category | Wrong Assumption | Why |
|---|---|---|---|
| Amazon OpenSearch Service | `bigdata` | `databases` | AWS classifies it under Analytics → Search analytics |
| AWS Glue | `bigdata` | `databases` | It's an ETL/data catalog service under Analytics |
| Amazon Kendra | `machinelearning` | `bigdata` | Enterprise search powered by ML, blogged under ML |
| Amazon Kinesis | `bigdata` | `serverless` | Streaming analytics, not serverless compute |
| Amazon QuickSight | `bigdata` | `operations` | BI tool under Analytics |
| Amazon MSK | `bigdata` | `opensource` | Managed Kafka under Analytics, not open source blog |
| Amazon ElastiCache | `databases` | `operations` | In-memory DB, not infra management |
| Amazon MemoryDB | `databases` | `operations` | Redis-compatible DB |
| AWS App Runner | `containers` | `serverless` | Container service, not serverless compute |
| Amazon Bedrock | `machinelearning` | multiple | Always ML, even when used for search/agents |

When in doubt, search multiple categories rather than guessing one.
**For very recent features (< 30 days old), always include `whatsnew`.**

## Usage with sitemap_feed_extractor.py

```bash
# Get top 10 recent What's New announcements ($SKILL_DIR resolved by parent)
uv run $SKILL_DIR/scripts/sitemap_feed_extractor.py \
  "https://aws.amazon.com/about-aws/whats-new/recent/feed/" --top 10 --json

# Get top 5 recent ML blog posts
uv run $SKILL_DIR/scripts/sitemap_feed_extractor.py \
  "https://aws.amazon.com/blogs/machine-learning/feed/" --top 5 --json

# Get top 5 recent security blog posts
uv run $SKILL_DIR/scripts/sitemap_feed_extractor.py \
  "https://aws.amazon.com/blogs/security/feed/" --top 5 --json
```

The `--json` flag outputs structured data with URLs, titles, and dates that
can be parsed programmatically.

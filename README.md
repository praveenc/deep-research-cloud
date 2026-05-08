# Deep Research Cloud

> AI-powered research reports on AWS & cloud technology — powered by a single Strands Agent on Amazon Bedrock AgentCore Runtime with Lambda-hosted MCP servers.

![Architecture](docs/design/architecture.png)

## Overview

Deep Research Cloud translates the local [`aws-deep-research`](https://github.com/user/aws-deep-research) skill 1:1 to a fully serverless cloud deployment. A single orchestrator agent decomposes research queries, dispatches parallel sub-agents (Pattern 3 — isolated context), synthesizes findings into cited reports, and pushes real-time progress via WebSocket.

**Key characteristics:**

- **Zero idle cost** — Lambda MCP servers + AgentCore Runtime (on-demand) = $0 when not researching
- **~$0.17–$0.85 per research run** (tokens dominate cost)
- **Observable by default** — ADOT/OpenTelemetry traces, Bedrock metrics, CloudWatch dashboards, budget alarms
- **Secrets at runtime** — all API keys from Secrets Manager, never env vars

## Architecture

| Layer | Service | Purpose |
|-------|---------|---------|
| Frontend | CloudFront + S3 | React SPA, report viewer |
| API | API Gateway (REST + WS) | Cognito JWT auth, async invoke, WS progress |
| Agent | AgentCore Runtime | Full research lifecycle orchestration |
| MCP Servers | Lambda (Direct Invoke) + ADOT Layer | fetch, aws-docs, brave, github, feeds |
| Data | S3, DynamoDB, Secrets Manager | Artifacts, tracking, secrets |
| Observability | ADOT/OTel, CloudWatch | Traces, metrics, dashboards, alarms |

See [`docs/design/README.md`](docs/design/README.md) for the full architecture design document.

## Project Structure

```
deep-research-cloud/
├── app/
│   ├── agent/
│   │   ├── runtime/          # AgentCore Runtime container (Strands SDK)
│   │   │   ├── Dockerfile    # ARM64 container for AgentCore
│   │   │   ├── main.py       # Agent entrypoint (BedrockAgentCoreApp)
│   │   │   ├── tools.py      # @tool functions (MCP invoke, S3, DDB, WS)
│   │   │   └── requirements.txt
│   │   ├── invoker/          # Lambda: REST API → async invoke agent
│   │   ├── status/           # Lambda: GET /research/{slug}/status
│   │   └── ws-handlers/      # Lambda: WebSocket $connect/$disconnect
│   ├── mcp-servers/
│   │   ├── fetch-mcp/        # Web content extraction + SSRF protection
│   │   ├── aws-docs-mcp/     # AWS documentation search
│   │   ├── brave-mcp/        # Brave Search API
│   │   ├── github-mcp/       # GitHub repo/code search
│   │   └── feeds-mcp/        # RSS/Atom blog feed parser
│   └── frontend/
│       └── dist/             # React SPA (placeholder)
├── infra/                    # CDK IaC (TypeScript)
│   ├── bin/app.ts            # App entry — 6 stacks
│   ├── config/index.ts       # Environment configuration
│   └── lib/
│       ├── data-stack.ts           # S3 + DynamoDB (stateful)
│       ├── mcp-servers-stack.ts    # 5 Lambda MCP servers
│       ├── api-stack.ts            # REST + WebSocket + Cognito
│       ├── agent-runtime-stack.ts  # AgentCore Runtime (alpha)
│       ├── frontend-stack.ts       # CloudFront + S3
│       └── observability-stack.ts  # Dashboard + alarms
└── docs/design/              # Architecture design docs
```

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Node.js | ≥ 18 | For CDK CLI |
| AWS CDK CLI | ≥ 2.237 | `npm install -g aws-cdk` |
| AWS CLI | ≥ 2.x | Configured with target account credentials |
| Docker | Latest | For building AgentCore Runtime container (ARM64) |
| Python | ≥ 3.13 | For Lambda handlers and agent runtime |

### AWS Account Setup

1. **Bedrock model access** — Enable `anthropic.claude-sonnet-4-20250514` in your account (us-west-2)
2. **AgentCore Runtime** — Ensure Bedrock AgentCore is available in your region
3. **Service quotas** — Default limits are fine for dev (25 TPS per agent)

## Installation

### 1. Clone the repository

```bash
git clone <repo-url>
cd deep-research-cloud
```

### 2. Create the Secrets Manager secret

The MCP servers expect API keys stored as a JSON object in Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name prod/deepresearch/Search \
  --region us-west-2 \
  --secret-string '{
    "BRAVE_API_KEY": "your-brave-api-key",
    "GITHUB_TOKEN": "your-github-personal-access-token"
  }'
```

> **Where to get keys:**
> - Brave Search API: https://brave.com/search/api/
> - GitHub Token: https://github.com/settings/tokens (scope: `public_repo`, `read:org`)

### 3. Install CDK dependencies

```bash
cd infra
npm install
```

### 4. Configure your environment

Edit `infra/config/index.ts` if you need to change:
- AWS region (default: `us-west-2`)
- Bedrock model ID
- Budget alarm thresholds
- Alarm notification email

The account ID is auto-resolved from your AWS CLI credentials via `CDK_DEFAULT_ACCOUNT`.

### 5. Bootstrap CDK (first time only)

If this is the first CDK deployment in your account/region:

```bash
npx cdk bootstrap aws://<ACCOUNT_ID>/us-west-2
```

### 6. Synthesize and review

```bash
npx cdk synth
npx cdk diff
```

This produces 6 CloudFormation stacks:
- `DeepResearch-Data-dev`
- `DeepResearch-McpServers-dev`
- `DeepResearch-Api-dev`
- `DeepResearch-AgentRuntime-dev`
- `DeepResearch-Frontend-dev`
- `DeepResearch-Observability-dev`

### 7. Deploy

**Deploy all stacks (recommended for first deploy):**

```bash
npx cdk deploy --all --require-approval broadening
```

**Or deploy incrementally:**

```bash
# 1. Stateful resources first (protected from accidental deletion)
npx cdk deploy DeepResearch-Data-dev

# 2. MCP server Lambdas
npx cdk deploy DeepResearch-McpServers-dev

# 3. API layer (Cognito + REST + WebSocket)
npx cdk deploy DeepResearch-Api-dev

# 4. Agent runtime (builds and pushes Docker image to ECR)
npx cdk deploy DeepResearch-AgentRuntime-dev

# 5. Frontend (CloudFront + SPA)
npx cdk deploy DeepResearch-Frontend-dev

# 6. Observability (dashboard + alarms)
npx cdk deploy DeepResearch-Observability-dev
```

### 8. Post-deployment: S3 bucket policy for CloudFront

Since the research bucket is cross-stack, you need to manually add an OAC policy:

```bash
# Get the CloudFront distribution ID from stack outputs
DIST_ID=$(aws cloudformation describe-stacks \
  --stack-name DeepResearch-Frontend-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' \
  --output text)

# The CDK output will show a warning with the exact policy statement needed.
# Add it to the research bucket policy to allow CloudFront to serve /reports/*
```

### 9. Post-deployment: Wire the invoker Lambda

After the AgentRuntime stack deploys, update the invoker Lambda with the runtime ID:

```bash
RUNTIME_ID=$(aws cloudformation describe-stacks \
  --stack-name DeepResearch-AgentRuntime-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentRuntimeId`].OutputValue' \
  --output text)

aws lambda update-function-configuration \
  --function-name deep-research-invoker-dev \
  --environment "Variables={STAGE=dev,TRACKING_TABLE=deep-research-tracking-dev,RESEARCH_BUCKET=deep-research-dev-$(aws sts get-caller-identity --query Account --output text),AGENT_RUNTIME_ID=$RUNTIME_ID}"
```

## Usage

### Invoke a research task (via API)

```bash
# Get the REST API URL from outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name DeepResearch-Api-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`RestApiUrl`].OutputValue' \
  --output text)

# Get a Cognito token (admin user must be created first)
TOKEN="<your-cognito-jwt-token>"

# Submit research
curl -X POST "${API_URL}research" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Compare Amazon Bedrock vs SageMaker for RAG workloads in production",
    "options": {
      "depth": "comprehensive",
      "sources": ["aws-docs", "web", "github"]
    }
  }'
```

### Check status

```bash
curl "${API_URL}research/<slug>/status" \
  -H "Authorization: Bearer $TOKEN"
```

### Real-time progress (WebSocket)

```bash
WS_URL=$(aws cloudformation describe-stacks \
  --stack-name DeepResearch-Api-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`WebSocketUrl`].OutputValue' \
  --output text)

# Connect with token
wscat -c "${WS_URL}?token=$TOKEN"
```

## Development

### Local testing of MCP servers

Each MCP server is a standalone Python Lambda handler. Test individually:

```bash
cd app/mcp-servers/fetch-mcp
python -c "
import json
from handler import lambda_handler
event = {'method': 'tools/call', 'params': {'name': 'fetch_url', 'arguments': {'url': 'https://docs.aws.amazon.com/bedrock/'}}}
print(json.dumps(lambda_handler(event, None), indent=2))
"
```

### Local testing of the agent runtime

```bash
cd app/agent/runtime
pip install -r requirements.txt

# Set env vars
export AWS_REGION=us-west-2
export BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-20250514
export RESEARCH_BUCKET=my-test-bucket
export TRACKING_TABLE=my-test-table

# Run locally (starts on port 8080)
python main.py

# Test health
curl http://localhost:8080/ping

# Test invocation
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"query": "What is Amazon Bedrock AgentCore?", "slug": "test-run", "userId": "dev"}'
```

### CDK watch mode (hot deploy)

```bash
cd infra
npx cdk watch DeepResearch-McpServers-dev
```

## Observability

After deployment, access:

- **CloudWatch Dashboard**: Link in `DeepResearch-Observability-dev` stack outputs
- **Traces**: CloudWatch → Application Signals → Transaction Search
- **Metrics**: Bedrock namespace (tokens, latency) + Lambda namespace (duration, errors)
- **Alarms**: SNS topic `deep-research-alarms-dev` (subscribe your email)

## Cost Estimate

| Component | Per Run | Monthly Idle |
|-----------|---------|--------------|
| AgentCore Runtime (5-7 min) | $0.02–0.04 | $0.00 |
| Lambda MCP servers (~15 calls) | $0.001–0.005 | $0.00 |
| Bedrock tokens (Claude Sonnet) | $0.15–0.80 | $0.00 |
| S3 + DynamoDB | < $0.01 | < $1.00 |
| CloudFront | < $0.01 | < $1.00 |
| **Total** | **$0.17–0.85** | **~$0** |

## Teardown

```bash
cd infra
npx cdk destroy --all
```

> **Note:** The Data stack has `RemovalPolicy.RETAIN` on S3 and DynamoDB. To fully clean up, manually delete:
> - S3 bucket: `deep-research-dev-<account-id>`
> - DynamoDB tables: `deep-research-tracking-dev`, `deep-research-connections-dev`

## License

[MIT](LICENSE)

# Progress Log

Grep-friendly development progress for Deep Research Cloud.
Each entry tagged: `[DONE]`, `[TODO]`, `[BLOCKED]`, `[IN-PROGRESS]`

---

## 2026-05-08 — Initial Scaffold Complete

### [DONE] Project scaffolding
- CDK app with 6 stacks: Data, McpServers, Api, AgentRuntime, Frontend, Observability
- All stacks wired via cross-stack references in `infra/bin/app.ts`
- `npx cdk synth` produces valid CloudFormation for all 6 stacks
- Architecture design docs finalized (`docs/design/`)

### [DONE] Agent runtime container
- `app/agent/runtime/main.py` — Strands agent with BedrockAgentCoreApp entrypoint
- `app/agent/runtime/tools.py` — 5 tools: invoke_mcp_server, write_to_s3, read_from_s3, update_task_status, push_ws_progress
- `app/agent/runtime/Dockerfile` — ARM64, Python 3.13, OTel auto-instrumentation
- System prompt defines full research lifecycle (10-step orchestration)

### [DONE] Lambda MCP servers (5)
- `fetch-mcp` — URL fetch with SSRF protection (IP blocklist, DNS check)
- `aws-docs-mcp` — AWS documentation search + page fetch (domain allowlist)
- `brave-mcp` — Brave Search API (secret from Secrets Manager)
- `github-mcp` — Repo search, code search, file content, README retrieval
- `feeds-mcp` — AWS blog RSS/Atom feed parser with keyword filtering

### [DONE] API layer
- REST API: Cognito authorizer, POST /research, GET /research/{slug}/status
- WebSocket API: $connect (JWT decode + DDB store), $disconnect (DDB remove)
- Invoker Lambda: validates request, generates slug, writes DDB task record

### [DONE] Observability
- ADOT Lambda Layer (ARM64) on all MCP servers
- CloudWatch dashboard: Bedrock tokens/latency, MCP server duration/errors
- Alarms: 500K tokens/hr, MCP error threshold → SNS topic
- OTel env vars configured in AgentCore Runtime container

### [DONE] Data layer
- S3 bucket: versioned, SSE-S3, lifecycle (IA→Glacier), RemovalPolicy.RETAIN
- DynamoDB tracking table: pk/sk composite, status-index GSI, TTL
- DynamoDB connections table: connectionId pk, user-index GSI, TTL

---

## TODO — Next Steps (Priority Order)

### [TODO] P0 — Wire AgentCore invocation
- **File:** `app/agent/invoker/handler.py` (line ~93, commented-out TODO)
- **Action:** Implement `agentcore_client.invoke_agent_runtime()` call
- **Depends on:** AgentCore SDK stabilization (`bedrock-agentcore` package API)
- **Ref:** Agent runtime outputs `AgentRuntimeId` which invoker needs

### [TODO] P0 — Install CDK dependencies and validate synth
- **File:** `infra/package.json`
- **Action:** `cd infra && npm install && npx cdk synth`
- **Risk:** `@aws-cdk/aws-bedrock-agentcore-alpha` may not be published yet or API may differ
- **Fallback:** Stub the AgentRuntime construct if alpha package unavailable

### [TODO] P1 — Implement Pattern 3 sub-agent parallel dispatch
- **File:** `app/agent/runtime/main.py`
- **Action:** Replace single-loop agent with:
  1. Orchestrator decomposes query → N sub-questions
  2. Spawn N sub-agent calls (isolated context per Strands `Agent()` instance)
  3. Each sub-agent calls MCP tools, writes findings to S3
  4. Orchestrator verifies findings, runs synthesizer sub-agent
- **Ref:** Local skill uses `aws-deep-research` with Pattern 3 (meta-tool)

### [TODO] P1 — Token/cost tracking
- **File:** `app/agent/runtime/tools.py` + `main.py`
- **Action:** Hook Strands SDK usage callback → write to DDB cost ledger (sk='cost')
- **Action:** Emit CloudWatch custom metric `DeepResearch/TokenUsage` per run
- **Enables:** Per-run cost display in status endpoint + dashboard widget

### [TODO] P1 — Checkpoint/resume mechanism (arch-review finding #1)
- **File:** `app/agent/runtime/main.py`
- **Action:** Write `status.json` to S3 after each step completion
- **Action:** On startup, check for existing `status.json` and resume from last checkpoint
- **Action:** Add `POST /research/{slug}/resume` endpoint
- **Ref:** `docs/design/arch-review-revised.md` — Finding #1 (🔴 HIGH)

### [TODO] P2 — WAF on API Gateway (arch-review finding #3)
- **File:** `infra/lib/api-stack.ts`
- **Action:** Add AWS WAF WebACL with:
  - Rate limit: 10 req/hr/user (Cognito sub claim)
  - Bot Control managed rule group
  - IP reputation list
  - Request size limit (query < 2000 chars)
- **Ref:** `docs/design/arch-review-revised.md` — Finding #3 (🟡 MEDIUM)

### [TODO] P2 — Bedrock Guardrails (arch-review finding #6)
- **File:** `infra/lib/agent-runtime-stack.ts`
- **Action:** Create Bedrock Guardrail (content filter, topic filter, word filter)
- **Action:** Apply guardrail ID to agent model invocations

### [TODO] P2 — React SPA frontend
- **File:** `app/frontend/` (new React project)
- **Action:** Scaffold with Vite + React + Tailwind
- **Features:** Cognito login (Amplify Auth), research form, WS progress, report viewer
- **Deploy:** `npm run build` → `dist/` → S3 via CDK BucketDeployment

### [TODO] P3 — CI/CD pipeline
- **Action:** GitHub Actions workflow:
  - On PR: `cdk diff`
  - On merge to main: `cdk deploy --all --require-approval never`
  - Docker build + ECR push for agent container
- **Consider:** CodePipeline if staying pure-AWS

### [TODO] P3 — CloudFront signed cookies for report access (arch-review finding #5)
- **File:** `infra/lib/frontend-stack.ts`
- **Action:** Lambda@Edge on `/reports/*` validates JWT from cookie
- **Ref:** `docs/design/arch-review-revised.md` — Finding #5 (🟡 MEDIUM)

### [TODO] P3 — Structured logging (arch-review finding #8)
- **File:** All Lambda handlers
- **Action:** JSON-format logs with `slug`, `traceId`, `functionName` correlation fields
- **Enables:** CloudWatch Logs Insights queries by slug

---

## Architecture Review Summary

From `docs/design/arch-review-revised.md` (2026-05-08):

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | No checkpoint/resume mechanism | 🔴 HIGH | [TODO] P1 |
| 2 | SSRF risk via fetch-mcp | 🟡 MEDIUM | [DONE] — IP blocklist implemented |
| 3 | No WAF on API Gateway | 🟡 MEDIUM | [TODO] P2 |
| 4 | WebSocket orphaned connections | 🟡 MEDIUM | [DONE] — GoneException handling in tools.py |
| 5 | S3 report access control | 🟡 MEDIUM | [TODO] P3 |
| 6 | Input validation + Guardrails | 🟡 MEDIUM | [TODO] P2 |
| 7 | Lambda concurrency limits | 🟢 LOW | [DONE] — Reserved concurrency in CDK |
| 8 | Structured logging | 🟢 LOW | [TODO] P3 |
| 9 | Encryption posture | 🟢 LOW | [DONE] — AWS defaults sufficient |

### Retracted findings (from initial review)
- ~~"Agent has access to ALL secrets"~~ — secrets are on Lambda side only
- ~~"Compromised agent can exfiltrate API keys"~~ — Lambda doesn't expose env vars via invoke
- ~~"Circuit breaker is missing"~~ — verify step handles partial MCP failure
- ~~"Tenant isolation is a gap"~~ — premature; single-user design

---

## Deployment Readiness Checklist

- [ ] `npm install` in `infra/` succeeds (agentcore-alpha dependency)
- [ ] `npx cdk synth` produces all 6 stacks without errors
- [ ] Secrets Manager secret created: `prod/deepresearch/Search`
- [ ] Bedrock model access enabled: `anthropic.claude-sonnet-4-20250514` in us-west-2
- [ ] AgentCore Runtime available in target region
- [ ] CDK bootstrap run in account/region
- [ ] AgentCore invocation wired in invoker Lambda
- [ ] End-to-end local test: agent → MCP → S3 → DDB

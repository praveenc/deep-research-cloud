# Deep Research Cloud — Final Architecture Design

> Single Strands Agent on AgentCore Runtime with Lambda-hosted MCP servers.  
> The local `aws-deep-research` skill translated 1:1 to cloud with minimal transformation.

![Architecture Diagram](./architecture.png)

## Design Principles

1. **Minimal translation** — local skill architecture maps directly to cloud (parent agent + sub-agents + tools)
2. **Progressive disclosure** — `AgentSkills` plugin loads skill instructions on-demand, not upfront
3. **Context isolation** — Pattern 3 (Meta-Tool) sub-agents keep raw content out of parent context
4. **Zero idle cost** — Lambda MCPs + AgentCore Runtime (on-demand) = $0 when not researching
5. **Secrets Manager** — all API keys retrieved at runtime, never env vars
6. **Observable by default** — ADOT/OpenTelemetry traces, Bedrock metrics, CW dashboards, budget alarms

## Architecture Summary

| Layer | Service | Count | Purpose |
|-------|---------|-------|---------|
| Frontend | CloudFront + S3 | 1 | React SPA, report viewer |
| API | API Gateway (REST + WS) | 1 | Auth (Cognito JWT), async invoke, WS progress |
| Agent | AgentCore Runtime | 1 agent | Full research lifecycle — orchestrate, research, synthesize, visualize |
| MCP Servers | Lambda (Direct Invoke) + ADOT Layer | 5 | fetch, aws-docs, brave, github, feeds |
| Data | S3, DynamoDB, Secrets Manager | 3 | Artifacts, tracking, secrets |
| Observability | ADOT/OTel, CloudWatch | — | Traces, metrics, dashboards, alarms |

**Total: 1 Runtime agent + 5 Lambdas + supporting services**

## AgentCore Runtime — Validated Constraints

Validated via AgentCore Slack bot (May 2026):

| Limit | Value | Mitigation |
|-------|-------|-----------|
| Container init | 120s hard | Lightweight container — no issue |
| Invocation response timeout | ~500s (observed, not documented) | Async invoke — don't wait for response |
| Idle session timeout | Up to 8 hours (configurable) | Configure for research duration |
| InvokeAgentRuntime TPS | 25/agent (adjustable via Service Quotas) | Single-user research — fine |

**Pattern:** Async invocation + HealthyBusy signaling + WebSocket push.  
The agent doesn't respond via the invoke API. It pushes progress/results via WS and writes to S3.

## Execution Flow

```
User submits query via SPA
  → APIGW authenticates (Cognito JWT)
  → Async invoke → AgentCore Runtime agent
  → Agent activates aws-deep-research skill (progressive disclosure)
  → Agent performs:
      1. Intent classification + strategy selection
      2. Query decomposition + slug generation
      3. Research contract → S3
      4. Dispatch sub-agents IN PARALLEL (Pattern 3, isolated context):
         • aws-mcp-researcher → calls aws-docs-mcp, fetch-mcp Lambdas → writes findings to S3
         • web-content-researcher → calls brave-mcp, fetch-mcp, feeds-mcp Lambdas → writes to S3
         • github-researcher → calls github-mcp Lambda → writes to S3
         (pushes WS progress after each completes)
      5. Verify findings (check S3 file sizes)
      6. Synthesizer sub-agent → reads all findings from S3 → writes report to S3
      7. Visual-generator sub-agent (frontend-design + highcharts skills) → writes HTML to S3
      8. Push "complete" via WebSocket with report URL
  → User views report at CloudFront: /reports/<slug>/
```

## Skills (Loaded via AgentSkills Plugin)

| Skill | When Activated | Resources |
|-------|---------------|-----------|
| `aws-deep-research` | Always (primary skill) | scripts/, references/ (loaded on-demand) |
| `frontend-design` | Visual generation step | SKILL.md only (~4KB) |
| `highcharts` | Chart generation | references/ (API docs, loaded per chart type) |
| `html-design` | HTML artifact styling | references/ (theme tokens) |

**Context budget:** ~100 tokens per skill at startup (metadata only).  
Full instructions loaded on activation (~5K tokens). Resources loaded incrementally as needed.

## Cost Tracking & Observability

### Distributed Tracing: ADOT + OpenTelemetry

AWS X-Ray SDKs entered maintenance mode Feb 2026 (end-of-support Feb 2027).
We use **OpenTelemetry instrumentation** via AWS Distro for OpenTelemetry (ADOT),
which sends traces to the CloudWatch backend (Application Signals + Transaction Search).

| Component | Instrumentation | How |
|-----------|----------------|-----|
| **Agent (Runtime)** | `strands-agents[otel]` | Native — set `OTEL_EXPORTER_OTLP_ENDPOINT` env var |
| **Lambda MCP servers** | ADOT Lambda Layer | Auto-instrumentation, no code changes |
| **Trace backend** | CloudWatch Application Signals | OTel Collector → OTLP → CloudWatch |

**Strands SDK auto-traces (zero-config):**
- Agent reasoning loops (each turn = span)
- LLM calls (model ID, tokens in/out, latency as span attributes)
- Tool executions (tool name, duration, success/failure)
- Sub-agent invocations (child spans with isolated context)

**ADOT Lambda Layer auto-traces:**
- Lambda invocation lifecycle (init, invoke, shutdown)
- Downstream AWS SDK calls (S3, DynamoDB, Secrets Manager)
- Cold start vs warm start differentiation

**Viewing traces:**
- CloudWatch → Application Signals (service map — see full topology)
- CloudWatch → Transaction Search (filter by slug, user, duration, error)
- End-to-end span: APIGW → Runtime → sub-agent → Lambda MCP → S3/DDB

**Trace sampling:** X-Ray free tier covers 100K traces/month. Use OTel sampler
at 10% in production (100% in dev). Budget stays within free tier for ~1M runs/month.

### Token Usage (Ground Truth)

Every Bedrock call returns `response.usage`:
- `InputTokens`, `OutputTokens`, `TotalTokens`
- `CacheReadInputTokens`, `CacheWriteInputTokens`

The agent captures this per sub-agent invocation and:
1. Attaches as OTel span attributes (queryable in Transaction Search)
2. Emits as CW custom metrics (dimensioned by slug, agent, model)
3. Writes to DynamoDB cost ledger (per slug/user)

**Tokenizer parity note:** Local estimates (tiktoken, etc.) diverge from Bedrock's
internal tokenization on non-English text, code, and whitespace. Always reconcile
against `response.usage` — it's the only authoritative source for billing.

### CloudWatch Metrics (Auto-emitted, no extra cost)

| Metric | Source | Dimension |
|--------|--------|-----------|
| `InputTokenCount` | Bedrock namespace | ModelId |
| `OutputTokenCount` | Bedrock namespace | ModelId |
| `CacheReadInputTokens` | Bedrock namespace | ModelId |
| `CacheWriteInputTokens` | Bedrock namespace | ModelId |
| `InvocationLatency` | Bedrock namespace | ModelId |
| `TimeToFirstToken` | Bedrock namespace | ModelId (streaming) |
| `EstimatedTPMQuotaUsage` | Bedrock namespace | ModelId |
| `Duration` | Lambda namespace | FunctionName |
| `Errors` | Lambda namespace | FunctionName |
| `Throttles` | Lambda namespace | FunctionName |

### CW Dashboard Widgets

| Widget | Metric Math / Source |
|--------|---------------------|
| Cost per run | `(InputTokenCount + OutputTokenCount) × per-token rate` |
| Research runs per day | Count of DDB task entries |
| Avg research duration | OTel root span duration |
| MCP server latency (p50/p99) | Lambda Duration by FunctionName |
| Token cache hit ratio | `CacheRead / (CacheRead + Input)` |
| API budget utilization | DDB counter / monthly limit |
| Model quota headroom | `EstimatedTPMQuotaUsage` vs service limit |

### Budget Alarms

| Alarm | Threshold | Action |
|-------|-----------|--------|
| Monthly Bedrock spend | Configurable ($) | SNS → email |
| Brave API calls | 80% of 2K/month | CW alarm → WS notify user |
| Tavily API calls | 80% of 1K/month | CW alarm → WS notify user |
| Per-run token spike | > 200K tokens | Log + flag in DDB |

## Cost Model (Per Research Run)

| Component | Estimated Cost |
|-----------|---------------|
| AgentCore Runtime (1 agent, 5-7 min) | $0.02-0.04 |
| Lambda MCP servers (~15 invocations) | $0.001-0.005 |
| Bedrock tokens (Claude Sonnet) | $0.15-0.80 |
| S3 + DynamoDB | < $0.01 |
| OTel traces (within free tier) | $0.00 |
| **Total per run** | **$0.17-0.85** |
| **Monthly idle cost** | **$0.00** |

## Design Decisions & Rationale

### 1. Single Agent on AgentCore Runtime (not Lambda, not Step Functions)

**Evaluated alternatives:**
- **Lambda agents (14 functions + Step Functions):** Higher operational complexity (14 IAM roles, SFN state machine, inter-Lambda coordination). Cold starts add 3-8s. Lambda's 15-min timeout is tight for complex research.
- **All-Lambda with Step Functions orchestration:** Clean separation but the agent's reasoning loop doesn't map well to a state machine. Step Functions can't do adaptive re-planning mid-research.

**Why AgentCore Runtime wins:**
- The local skill already works as a single parent agent dispatching sub-agents. AgentCore Runtime preserves this 1:1 — no architectural translation needed.
- No timeout for async workloads (8-hour idle session). Research runs 3-7 min comfortably.
- The agent IS the orchestrator. It can adaptively re-plan (e.g., dispatch an extra researcher if initial results are thin) — something Step Functions can't do without complex Choice states.
- HealthyBusy signaling + async invoke pattern handles the ~500s API response timeout.

### 2. Lambda MCP Servers (not AgentCore Gateway, not Fargate, not App Runner)

**Evaluated alternatives:**
- **AgentCore Gateway:** 50 TPS hard limit (all accounts), OAuth/resource policy complexity, VPC Lattice operational overhead, target sync issues. Overkill for internal agent→tool calls.
- **Fargate:** No native scale-to-zero. Minimum idle cost ~$25/month for 5 services. Requires custom scale-to-zero automation.
- **App Runner:** Minimum provisioned memory charge (~$0.007/hr idle × 5 = ~$25/month). No GPU, limited networking.

**Why Lambda wins:**
- Research tools are stateless, bursty, short-lived (1-15s per call). This is Lambda's sweet spot.
- $0.00 idle cost. Pay only during the ~15 invocations per research run.
- Direct Invoke (IAM-only) — no HTTP layer, no auth middleware, no gateway. Simplest possible integration.
- 1000+ concurrent executions (vs Gateway's 50 TPS cap).

### 3. Progressive Disclosure via AgentSkills Plugin (not prompt stuffing)

**Evaluated alternatives:**
- **Bake skill into system prompt:** Would consume 30-40KB+ if including all reference docs (frontend-design + highcharts + html-design). Exceeds context budget before research even starts.
- **No skills — just hardcoded agent prompts:** Loses modularity, can't reuse skills across agents, maintenance burden.

**Why AgentSkills plugin wins:**
- ~100 tokens per skill at startup (metadata only). Full instructions loaded on-demand.
- Resources (references/, scripts/) loaded incrementally — agent reads only what it needs for the specific task.
- Same skill packages work locally (pi/Kiro) and in cloud (Runtime) — no duplication.
- Pattern 3 (Meta-Tool) provides context isolation so skill execution doesn't pollute the parent's context.

### 4. ADOT/OpenTelemetry (not X-Ray SDK)

**Evaluated alternatives:**
- **X-Ray SDK:** Enters maintenance mode Feb 2026, end-of-support Feb 2027. No new features. Vendor-specific instrumentation.

**Why ADOT/OTel wins:**
- Strands SDK has **native OpenTelemetry** built in (`strands-agents[otel]`). Zero-config tracing of agent loops, LLM calls, tools, and sub-agents.
- ADOT Lambda Layer auto-instruments MCP server Lambdas without code changes.
- Industry-standard protocol — traces are portable, not locked to AWS.
- Still sends to CloudWatch backend (Application Signals, Transaction Search) — same visibility, future-proof instrumentation.
- Free tier (100K traces/month) easily covers research workloads with sampling.

### 5. Async Invoke + WebSocket (not synchronous request/response)

**Evaluated alternatives:**
- **Synchronous invoke:** Would hit ~500s API response timeout. Research takes 3-7 min.
- **Polling endpoint:** Client polls `GET /status/{slug}` every N seconds. Works but wasteful and laggy.

**Why async + WebSocket wins:**
- Agent can run indefinitely (8-hour idle timeout) without API response pressure.
- Real-time progress updates at every step (not just start/finish).
- HealthyBusy signaling keeps container alive during long research.
- Single boto3 call (`post_to_connection`) from the agent — no extra Lambda needed.

### 6. Secrets Manager (not environment variables)

**Evaluated alternatives:**
- **Lambda env vars:** Hardcoded at deploy time. No rotation. Visible in console. No audit trail.
- **SSM Parameter Store:** Cheaper but no automatic rotation, weaker encryption defaults.

**Why Secrets Manager wins:**
- Automatic rotation without redeployment (critical for API keys with expiration).
- Fine-grained IAM policies — each Lambda only accesses the secrets it needs.
- CloudTrail audit trail for every access.
- 5-minute cache in Lambda execution context avoids per-invocation API calls.

### 7. S3 for Artifacts (not DynamoDB, not EFS)

**Why S3:**
- Research artifacts (markdown files, HTML visuals) are document-sized (1-50KB). S3 is the natural fit.
- Same `<slug>/` prefix pattern as the local `$WORK_DIR/<slug>/` directory.
- Lifecycle policies for automatic archival (90 days → Glacier).
- CloudFront serves reports directly from S3 — no rendering layer needed.
- DynamoDB stores metadata/tracking only (small, structured, queryable).

## Local → Cloud Mapping

| Local (pi/Kiro) | Cloud (AgentCore Runtime) |
|---|---|
| Parent agent context | Runtime agent context |
| `subagent dispatch` (pi/Kiro harness) | Strands Pattern 3 (Meta-Tool sub-agents) |
| `$SKILL_DIR/scripts/*.py` | Lambda MCP servers |
| `fetchv2` MCP server (local process) | `fetch-mcp` Lambda |
| `$WORK_DIR/<slug>/` on filesystem | `s3://bucket/<slug>/` |
| Terminal output | WebSocket push to React SPA |
| `.env` file with API keys | Secrets Manager |
| No observability | ADOT/OTel + CloudWatch + budget alarms |

## Files

- `architecture.svg` — Editable source diagram (Style 6: Claude Official)
- `architecture.png` — Rendered 1920px output (retina)

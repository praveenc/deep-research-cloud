# Changelog

All notable changes to Deep Research Cloud are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/)

## [Unreleased]

### Added
- **Pre-processing Lambda** (`app/preprocess/`) implementing Steps 1, 2,
  3, and 1g of the local `aws-deep-research` skill in a single Strands
  `agent.structured_output` call. Pydantic schema with closed `Literal`
  types for intents (12), strategies (4), subagents (4), blog categories
  (9), query type (`aws`/`generic`), plus a strict slug validator (4–7
  tokens, 30–60 chars, kebab-case). Local-test mode (`PREPROCESS_LOCAL=1`)
  writes artifacts under `.preprocess-out/<slug>/`.
- **CDK wiring for the Pre-processing Lambda** — `infra/lib/api-stack.ts`
  swaps `POST /research` from the old `InvokerHandler` to a new
  `PreprocessHandler` (Python 3.13, ARM64, 90s, 512 MB) with
  least-privilege IAM (S3 PutObject, DDB PutItem, scoped
  `bedrock:InvokeModel*` on the configured inference profile +
  `anthropic.claude-*` foundation models).
- **Two-Lambda + FastMCP target architecture** documented in
  `docs/design/README.md` and `docs/design/architecture.png/svg`. Adds
  slug, decomposition, contract, and findings-verification conventions
  carried over verbatim from the local skill.
- **CloudWatch logging** in the pre-processor: structured single-line
  JSON metrics (`preprocess.metrics`) plus a human-readable
  decomposition block matching the local skill's transparency rule.
- **`AGENTS.md`** (replacing `CLAUDE.md`) per the
  [agents.md](https://agents.md) convention — editor-agnostic project
  guide for AI coding assistants.
- **Global pi subagents** for shared CDK workflows: `cdk.ops`,
  `cdk.review`, and a generic `aws-docs` documentation lookup agent
  (auto-routes between AWS Knowledge MCP and Bedrock AgentCore MCP).

### Changed
- **Default LLM** unified to `us.anthropic.claude-opus-4-6-v1`
  (cross-region inference profile) across pre-processor, agent runtime,
  CDK config (dev + prod), and docs. Override via `BEDROCK_MODEL_ID`.
- **Architecture pivot** (BREAKING in design, not yet in deployed code):
  - Agent runtime: container on AgentCore Runtime → Lambda
  - MCP servers: 5 separate Lambdas → one FastMCP on AgentCore Runtime
  - Custom MCP tool surface shrinks from 5 to 4 (`fetch_url`,
    `brave_search`, `tavily_search`, `extract_feed`); upstream
    `awslabs.*-mcp-server@latest` packages are spawned via `uvx` inside
    the Runtime container, and AWS docs route directly to the GA AWS
    Knowledge MCP at `https://knowledge-mcp.global.api.aws` (Streamable
    HTTP, unauthenticated, rate-limited only).
  - `POST /research` semantics flip from "fire research" to "build
    research contract and return for client review".
  - `POST /research/{slug}/start` planned to dispatch the Agent Lambda
    after contract approval (follow-up PR).
- **Container name** standardized on `my-git-workspace` everywhere
  (was inconsistent with `gh-authenticated-container` in some docs).

### Fixed
- **Agent runtime boto3 timeouts** — disabled retries on the
  bedrock-agentcore client (default 60s read_timeout was spawning
  duplicate parallel agent runs); synthesizer watchdog 3min → 10min;
  BedrockModel `read_timeout` overridden to 600s.
- **Agent runtime `max_turns` removal** — replaced with cancel-based
  watchdog (`max_turns` was not a valid Strands argument).
- **Pydantic null-coercion** in `PreprocessResult`, `ContractData`, and
  `Decomposition` — Opus emits `null` for empty optional list fields
  where Sonnet emitted `[]`; `field_validator(mode="before")` now
  accepts both shapes.

### Removed
- `app/agent/invoker/` source (AgentCore self-invoke trampoline,
  obsoleted by `app/preprocess/handler.py` and the `POST /research`
  swap in PR #3).
- `.claude/` (slash commands published as global pi subagents instead).
- `.mcp.json` (Claude Code/Cursor-specific registry).
- `CLAUDE.md` (replaced by `AGENTS.md`).

### Not Yet Implemented
- **Agent Lambda** — Strands LLM-led parent + Pattern 3 sub-agents that
  fills `AGENT_LAMBDA_NAME` and dispatches research after
  pre-processing. Will replace `app/agent/runtime/` (deterministic
  Python workflow); the `app/agent/invoker/` self-invoke trampoline is
  already deleted.
- **FastMCP runtime container** with the corrected tool inventory
  (4 custom `@mcp.tool()` + `uvx` subprocesses for `awslabs.*` +
  Streamable HTTP forwarder for AWS Knowledge MCP).
- **Frontend contract review screen** for `needs_approval=true` responses.
- **Lambda Authorizer** (Cognito JWT) — currently using the built-in
  `apigateway.CognitoUserPoolsAuthorizer`.
- **WAF on API Gateway**, custom domain, research history list,
  per-user rate limiting.

## Pre-pivot baseline (kept for history)

### Added
- CDK infrastructure: 6 stacks (Data, McpServers, Api, AgentRuntime, Frontend, Observability)
- AgentCore Runtime container with Strands SDK single-agent orchestrator
- 5 Lambda MCP servers: fetch-mcp, aws-docs-mcp, brave-mcp, github-mcp, feeds-mcp
- REST API: POST /research (async invoke), GET /research/{slug}/status
- WebSocket API: real-time progress push from agent to client
- Cognito User Pool authentication (admin-created users only)
- SSRF protection in fetch-mcp (IP blocklist, scheme validation, DNS resolution check)
- ADOT/OTel auto-instrumentation on all Lambda MCP servers
- CloudWatch dashboard: Bedrock tokens, latency, MCP server metrics
- Budget alarms: token usage spike (500K/hr), MCP server error thresholds
- S3 lifecycle rules: IA at 90d, Glacier at 365d
- DynamoDB TTL on task records (90d) and connections (24h)
- Frontend placeholder (static HTML, React SPA pending)
- Architecture design docs and revised security/ops review

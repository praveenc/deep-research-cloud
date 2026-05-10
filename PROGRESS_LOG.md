# Deep Research Cloud — Progress Log

## Implementation Roadmap

| # | Priority | Task | Status |
|---|----------|------|--------|
| 1 | 🔴 Critical | Wire AgentCore invocation in invoker Lambda | ✅ Done (pre-pivot) |
| 2 | 🔴 Critical | Install CDK deps & verify synth (resolve agentcore-alpha) | ✅ Done |
| 3 | 🟠 High | Add sub-agent parallel dispatch (Pattern 3) | ✅ Done (pre-pivot) |
| 4 | 🟠 High | Token/cost tracking (DDB ledger + CW metrics) | ✅ Done |
| 5 | 🟡 Medium | End-to-end local test harness | ✅ Done |
| 6 | 🟡 Medium | Frontend React SPA (Cognito + WS + report viewer) | ✅ Done |
| 7 | 🟢 Low | CI/CD pipeline (GitHub Actions) | ✅ Done |

### Post-pivot roadmap (new design)

| # | Priority | Task | Status |
|---|----------|------|--------|
| 8 | 🔴 Critical | Pre-processing Lambda (Steps 1–3 + 1g) | ✅ Done (PR #1) |
| 9 | 🔴 Critical | CDK wiring for Pre-processing Lambda (`POST /research`) | ✅ Done (PR #3) |
| 10 | 🔴 Critical | Agent Lambda (Strands LLM-led + Pattern 3 sub-agents) | ⚪ Pending |
| 11 | 🔴 Critical | FastMCP runtime container (4 custom + `uvx awslabs.*` + AWS Knowledge MCP forwarder) | ⚪ Pending |
| 12 | 🟠 High | Frontend contract-review screen (complex queries) | ⚪ Pending |
| 13 | 🟠 High | `POST /research/{slug}/start` route + Agent Lambda dispatch | ⚪ Pending |
| 14 | 🟡 Medium | Lambda Authorizer (Cognito JWT) replacing built-in REST authorizer | ⚪ Pending |
| 15 | 🟡 Medium | Remove `app/agent/{invoker,runtime}/` after Agent Lambda lands | ⚪ Pending |
| 16 | 🟢 Low | WAF on API Gateway, custom domain, history list, rate limits | ⚪ Pending |

---

## Log

### 2025-05-08

- **Project scaffolded** — 6 CDK stacks, 5 Lambda MCP servers, AgentCore Runtime container, REST + WS APIs, Cognito, observability. All files committed.
- **Identified 7 implementation gaps** — prioritized above.

### 2025-05-08 — Implementation Sprint

1. **Invoker Lambda wired** (`app/agent/invoker/handler.py`)
   - Added `bedrock-agentcore-runtime` client
   - Implements `invoke_runtime()` with async fire-and-forget
   - Error handling: marks task FAILED if invoke fails
   - CDK updated: added `AGENT_ENDPOINT_NAME` env var + `InvokeRuntimeStream` IAM action

2. **CDK synth verified** — `npm install` + `npx cdk synth` passes cleanly
   - All 6 stacks synthesize without errors
   - Only expected warnings (deprecation on `pointInTimeRecovery`, OAC imported bucket)

3. **Sub-agent parallel dispatch** (`app/agent/runtime/tools_subagent.py`)
   - `dispatch_sub_agents` tool: ThreadPoolExecutor with configurable concurrency
   - `run_synthesizer` tool: isolated Agent instance for report generation
   - Each sub-agent gets fresh conversation context (Pattern 3)
   - `main.py` refactored: separate prompts for orchestrator, researcher, synthesizer
   - Orchestrator uses `dispatch_sub_agents` + `run_synthesizer` as meta-tools

4. **Cost tracking** (`app/agent/runtime/cost_tracker.py`)
   - Thread-safe `CostTracker` class accumulates tokens across parallel sub-agents
   - Flushes to DDB (`sk: 'cost'`) at end of research run
   - Emits CloudWatch custom metrics (`DeepResearch` namespace)
   - Pricing constants for Claude Sonnet 4 (input/output/cache)
   - Integrated into `main.py` invoke flow — runs `finalize()` on success or failure

5. **Local test harness** (`app/agent/runtime/test_local.py`)
   - In-memory S3 + DynamoDB mocks
   - Optional `--mock-mcp` flag for fully offline testing
   - `--smoke` flag for quick validation
   - Prints S3 artifacts and report preview to stdout
   - Exit code 0/1 for CI integration

6. **Frontend React SPA** (`app/frontend/`)
   - Vite + React 19 + TypeScript
   - `auth.ts`: Cognito login/logout via amazon-cognito-identity-js
   - `api.ts`: typed API client (submitResearch, getResearchStatus)
   - `useWebSocket.ts`: real-time progress hook
   - `App.tsx`: login form → research form → progress bar → report viewer (react-markdown)
   - `styles.css`: clean, minimal design system
   - Config via Vite env vars (VITE_USER_POOL_ID, etc.)

7. **CI/CD pipeline** (`.github/workflows/deploy.yml`)
   - `validate` job: TypeScript check + CDK synth + diff on PRs
   - `build-frontend` job: Vite build with env vars injected
   - `deploy` job: OIDC auth + `cdk deploy --all` on push to main
   - Stack outputs printed to GitHub step summary

---

## Next Steps (Post-MVP)

- [ ] Wire Strands SDK `callback_handler` to feed `record_usage_callback` for real token counting
- [ ] Add `npm ci && npm run build` to the CDK Frontend stack's `BucketDeployment` source
- [ ] Generate `package-lock.json` for frontend (`cd app/frontend && npm install`)
- [ ] Add integration tests (deploy to a test account, run a real research query, assert report exists)
- [ ] Implement new-password-required flow in the frontend login
- [ ] Add research history list (query DDB by userId)
- [ ] Rate limiting per user (Cognito custom attribute or DDB counter)
- [ ] Custom domain + ACM certificate for CloudFront

---

### 2026-05-09 — Architecture Pivot + Pre-processing Lambda (PR #1, merged)

Mid-flight re-baseline from a single-container AgentCore Runtime agent
to a leaner two-Lambda + FastMCP topology. Landed via PR #1.

1. **Architecture pivot** (`docs/design/README.md` + `architecture.png/svg`)
   - Pre-processing Lambda → Agent Lambda → FastMCP on AgentCore Runtime
   - Contract approval flow for complex queries (3+ entities or version constraints)
   - All slug / decomposition / contract / findings-verification
     conventions carried over verbatim from the local skill

2. **Pre-processing Lambda** (`app/preprocess/`)
   - `handler.py` — single Strands `agent.structured_output(PreprocessResult)`
     call drives the whole plan; renders contract → S3, writes DDB
     tracking, emits CloudWatch logs (structured + human-readable),
     auto-dispatches to Agent Lambda for simple queries (no-op until
     `AGENT_LAMBDA_NAME` is set)
   - `models.py` — closed `Literal` types for every axis; strict slug
     validator enforcing 4–7 tokens, 30–60 chars, kebab-case
   - `prompts.py` — system prompt embeds `intent-patterns.md`,
     `blog-categories.md`, and `research-contract-guide.md` from the
     local skill verbatim (packaged under `skill_refs/`)
   - `test_local.py` — `PREPROCESS_LOCAL=1` runner with 5 fixture
     queries; validated end-to-end against real Bedrock

3. **Pydantic null-coercion fix**
   - Opus consistently emits `null` for empty optional list fields
     where Sonnet emitted `[]`. Added `field_validator(mode="before")`
     to `PreprocessResult`, `ContractData`, and `Decomposition` so
     both shapes validate.

4. **Default model unified** to `us.anthropic.claude-opus-4-6-v1`
   (cross-region inference profile) across pre-processor, agent
   runtime, README, CLAUDE.md, and CDK config (dev + prod).

5. **Agent runtime hardening** (existing pre-pivot code)
   - Disabled boto3 retries on bedrock-agentcore client (60s default
     read_timeout was spawning duplicate parallel runs)
   - Synthesizer watchdog 3 min → 10 min
   - BedrockModel `read_timeout` → 600 s
   - Removed invalid `max_turns` argument; replaced with cancel-based watchdog

6. **Project tooling**
   - `CLAUDE.md` → `AGENTS.md` per [agents.md](https://agents.md)
     convention; editor-agnostic
   - Removed `.claude/` slash commands and `.mcp.json` (Claude
     Code/Cursor-specific config)
   - Published global pi subagents `cdk.ops`, `cdk.review`,
     `aws-docs` for CDK workflows across any project
   - Standardized container name on `my-git-workspace` everywhere

### 2026-05-09 — MCP Tool Inventory Correction (PR #2, merged)

Honest-up about which MCP tools we actually own vs proxy.

- The local skill's "MCP servers" are mostly thin clients spawning
  `awslabs.*-mcp-server@latest` packages via `uvx`/stdio, or talking
  to the GA AWS Knowledge MCP at `https://knowledge-mcp.global.api.aws`.
  We do NOT reimplement any of those — we wrap them.
- Custom tool surface in our FastMCP runtime: 4 (`fetch_url`,
  `brave_search`, `tavily_search`, `extract_feed`).
- Upstream MCP servers spawned via `uvx` inside the Runtime:
  `awslabs.aws-pricing-mcp-server`,
  `awslabs.amazon-bedrock-agentcore-mcp-server`,
  `awslabs.git-repo-research-mcp-server`.
- AWS docs forwarded directly to AWS Knowledge MCP
  (`https://knowledge-mcp.global.api.aws`) over Streamable HTTP, no
  authentication needed (rate-limited only). Real upstream tools:
  `search_documentation`, `read_documentation`, `recommend`,
  `list_regions`, `get_regional_availability`, `retrieve_skill`.
- Initially wrote `aws-mcp.us-east-1.api.aws/mcp` + SigV4 (the older
  experimental endpoint); corrected to the GA URL above per
  https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server.

### 2026-05-09 — CDK Wiring for Pre-processing Lambda (PR #3, merged)

Wires the pre-processor into the deployment so `POST /research`
actually invokes it.

- `infra/lib/api-stack.ts`: replace `InvokerHandler` (self-invoking
  trampoline calling AgentCore Runtime) with `PreprocessHandler`.
- New Lambda: Python 3.13, ARM64, 90 s timeout, 512 MB,
  code asset `app/preprocess/`.
- Env: `STAGE`, `TRACKING_TABLE`, `RESEARCH_BUCKET`,
  `BEDROCK_MODEL_ID`, `AGENT_LAMBDA_NAME=""`.
- IAM (least privilege):
  - `dynamodb:PutItem` on tracking table
  - `s3:PutObject` on research bucket (`<slug>/research-contract.md`,
    `<slug>/plan.json`)
  - `bedrock:InvokeModel` + `bedrock:InvokeModelWithResponseStream`
    scoped to the configured inference profile and
    `anthropic.claude-*` foundation models (cross-region wildcard)
- `POST /research` API Gateway integration retargeted to the new
  Lambda. Old `InvokerHandler` resources removed cleanly.
- `cdk synth --quiet` clean across all 6 stacks; `cdk diff` shows
  exactly the expected swap (delete + add + retarget) with no
  collateral damage.
- `app/agent/invoker/` source kept for now; removed in a follow-up
  cleanup PR (see entry below).

### 2026-05-09 — Remove app/agent/invoker source (PR #6, merged)

Deletes the orphaned AgentCore self-invoke trampoline that PR #3
already stopped wiring into CDK.

- Deleted `app/agent/invoker/handler.py` (241 lines).
- Verified zero references via grep across the repo (only docs
  mentioned it, and those are updated here).
- `cdk synth --quiet` clean across all 6 stacks (verified via
  `cdk.ops` subagent).
- `AGENTS.md` Repository layout: `agent/invoker/` line removed.
- `CHANGELOG.md` [Unreleased] / Removed: bullet added; the Not Yet
  Implemented bullet for Agent Lambda updated to reflect that the
  trampoline is already gone.
- Successor: the Agent Lambda introduced in a later PR will fill
  `AGENT_LAMBDA_NAME` (currently `""`).

### 2026-05-10 — chore(agents): add repo-docs-keeper project subagent (PR #5, merged)

Adds a project-scoped pi subagent that owns post-merge bookkeeping for
`CHANGELOG.md`, `PROGRESS_LOG.md`, and `AGENTS.md`, so the parent agent
can hand off doc updates with a one-line pointer instead of reading
several KB of project docs into context.

- `.pi/agents/repo-docs-keeper.md` (new, +109 lines) — project-scoped
  pi subagent definition. Hard refusals: no `git
  commit/push/branch/merge/rebase/reset/tag`, no
  `gh pr merge/create/edit`, no edits outside the three doc files.
  Read-only discovery via `git log` / `git show --stat` /
  `gh pr view --json` inside `my-git-workspace`. Workflow: parse
  pointer → read existing files → decide which need updates → edit
  surgically → verify with `git diff --stat`. Enforces per-file
  conventions (CHANGELOG groupings, PROGRESS_LOG heading format,
  AGENTS triggers, status icons) and outputs a tight 3-line summary.
- `AGENTS.md` (+25 lines) — new "Project subagents" section pointing
  to `repo-docs-keeper`, plus a list of user-scoped helpers
  (`git-commits-docker`, `cdk.ops`, `cdk.review`, `aws-docs`) that
  pair well with the workflow but are not checked in.
- `PROGRESS_LOG.md` (+3 −3) — bookkeeping flips from the agent's
  self-test: roadmap row #9 (CDK wiring for Pre-processing Lambda)
  🟡 Open → ✅ Done; PR #2 and PR #3 dated-entry headings
  `(open)` → `(merged)`.

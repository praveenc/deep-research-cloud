# Deep Research Cloud — Progress Log

## Implementation Roadmap

| # | Priority | Task | Status |
|---|----------|------|--------|
| 1 | 🔴 Critical | Wire AgentCore invocation in invoker Lambda | ✅ Done |
| 2 | 🔴 Critical | Install CDK deps & verify synth (resolve agentcore-alpha) | ✅ Done |
| 3 | 🟠 High | Add sub-agent parallel dispatch (Pattern 3) | ✅ Done |
| 4 | 🟠 High | Token/cost tracking (DDB ledger + CW metrics) | ✅ Done |
| 5 | 🟡 Medium | End-to-end local test harness | ✅ Done |
| 6 | 🟡 Medium | Frontend React SPA (Cognito + WS + report viewer) | ✅ Done |
| 7 | 🟢 Low | CI/CD pipeline (GitHub Actions) | ✅ Done |

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

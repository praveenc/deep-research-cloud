# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A serverless AI research agent on AWS that decomposes research queries into sub-questions, dispatches parallel researcher sub-agents with MCP tools, and synthesizes findings into cited reports. Uses Strands Agents SDK on Amazon Bedrock AgentCore Runtime with Lambda-hosted MCP servers.

## Commands

### Infrastructure (CDK)

```bash
cd infra
npm ci                          # install dependencies
npx tsc --noEmit                # type-check CDK code
npx cdk synth --quiet           # synthesize CloudFormation templates
npx cdk diff                    # show pending changes
npx cdk deploy --all --require-approval broadening  # deploy all 6 stacks
npx cdk watch DeepResearch-McpServers-dev           # hot-deploy MCP Lambdas
```

Deploy individual stacks: `npm run deploy:data`, `deploy:mcp`, `deploy:api`, `deploy:agent`, `deploy:frontend`.

### Agent Runtime (local)

```bash
cd app/agent/runtime
pip install -r requirements.txt
python main.py                  # starts on port 8080
curl http://localhost:8080/ping # health check
```

### End-to-End Test

```bash
./scripts/post-deploy-test.sh                      # full setup + test
./scripts/post-deploy-test.sh --skip-setup         # rerun test only
./scripts/post-deploy-test.sh --query "..." --depth quick
```

### Frontend

```bash
cd app/frontend
npm ci && npm run build         # build SPA to dist/
```

## Architecture

The system is a **deterministic Python workflow** (not an LLM-controlled loop) that calls bounded LLM agents at specific steps:

1. **Decomposer** — LLM (no tools) breaks query into 3-5 sub-questions
2. **Researchers** — parallel sub-agents (up to 3 concurrent) each call MCP tools and write findings to S3
3. **Verifier** — Python code checks S3 for each findings file (size > 500 bytes = OK)
4. **Synthesizer** — LLM (write_to_s3 only) reads pre-assembled findings, writes final report
5. **Completion** — updates DynamoDB status + pushes WebSocket progress

### Key Design Decisions

- **No LLM loop at the orchestrator level** — prevents infinite loops and makes costs predictable
- **Each sub-agent has a cancel-based watchdog** (4 min researcher, 10 min synthesizer)
- **MCP servers are Lambda functions invoked via Direct Invoke** (IAM auth, not HTTP/stdio)
- **Async invocation pattern**: API Gateway -> invoker Lambda (202 response) -> self-invoke with `InvocationType='Event'` -> calls AgentCore synchronously in the background Lambda
- **DynamoDB key schema**: `pk = "{userId}#{slug}"`, `sk = "meta" | "status" | "cost"`

### Stack Dependency Order

```
Data (S3 + DDB) → McpServers → Api (Cognito + REST + WS) → AgentRuntime → Frontend
                                                                          → Observability
```

### Agent Tools (Strands @tool decorated)

All in `app/agent/runtime/tools.py`:
- `invoke_mcp_server(server_name, tool_name, arguments)` — calls Lambda MCP servers
- `write_to_s3(key, content)` / `read_from_s3(key)` — research artifact I/O
- `update_task_status(table_name, user_id, slug, status)` — DDB state
- `push_ws_progress(user_id, slug, message, step, progress_pct)` — WebSocket push

### MCP Servers (Lambda)

Each is a standalone Python handler receiving MCP JSON-RPC `tools/call` events:
- `fetch-mcp` — web content extraction with SSRF protection
- `aws-docs-mcp` — AWS documentation search
- `brave-mcp` — Brave Search API
- `github-mcp` — GitHub repo/code search
- `feeds-mcp` — RSS/Atom blog feed parser

## Configuration

- Environment config: `infra/config/index.ts` (region, model ID, budget thresholds)
- Secrets: `prod/deepresearch/Search` in Secrets Manager (BRAVE_API_KEY, GITHUB_TOKEN)
- Default model: `us.anthropic.claude-opus-4-6-v1` (cross-region inference profile)
- Default region: `us-west-2`

## Key Dependencies

- **Infrastructure**: `aws-cdk-lib ^2.237`, `@aws-cdk/aws-bedrock-agentcore-alpha`
- **Agent Runtime**: `strands-agents >=1.0.0`, `bedrock-agentcore >=0.1.0`, `boto3`, `aws-opentelemetry-distro`
- **Agent container**: Python 3.13, linux/arm64, port 8080, entrypoint via `opentelemetry-instrument`

## Git Commit Convention

**All git operations (commit, push, tag) must run inside the `gh-authenticated-container` docker container.**

```bash
# Check if container is running
docker ps --filter name=gh-authenticated-container --format '{{.Names}}'

# Start if not running (from the docker-github parent directory)
docker compose run -d --rm --name my-git-workspace git-workspace

# All git commands use this pattern:
docker exec gh-authenticated-container git -C /workspace/repos/deep-research-cloud <command>

# Examples:
docker exec gh-authenticated-container git -C /workspace/repos/deep-research-cloud status
docker exec gh-authenticated-container git -C /workspace/repos/deep-research-cloud add app/agent/runtime/main.py
docker exec gh-authenticated-container git -C /workspace/repos/deep-research-cloud commit -m "feat(agent): add watchdog timeout"
docker exec gh-authenticated-container git -C /workspace/repos/deep-research-cloud push origin main

# GitHub CLI:
docker exec gh-authenticated-container gh pr create --repo <owner/repo> ...
```

Use Conventional Commits: `<type>(scope): <description>`

- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`
- Imperative mood, subject line ≤50 chars, no trailing period
- Scope = folder/component name (e.g., `api`, `infra`, `agent`, `frontend`)
- Split unrelated changes into separate atomic commits
- Run linters before committing if available (`ruff` for Python, `prettier` for TS/JS)
- Stage explicit file paths, not `git add .`
- Never commit generated files (node_modules, __pycache__, .env, cdk.out)

## MCP Servers for Documentation

- **AWS documentation**: Always use `aws-knowledge-mcp-server` to search for latest AWS docs — do not assume or hallucinate AWS service details.
- **AgentCore documentation**: Always use `bedrock-agentcore-mcp-server` for anything related to Bedrock AgentCore (Runtime, Memory, Code Interpreter, Browser, Gateway, Observability, Identity).

## CI/CD

GitHub Actions (`.github/workflows/deploy.yml`):
- PRs: TypeScript compile + `cdk synth` + `cdk diff`
- Push to main: builds frontend, deploys all stacks via OIDC role assumption

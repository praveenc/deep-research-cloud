# AGENTS.md

Guidance for AI coding assistants (Claude Code, Codex CLI, Cursor, Aider,
etc.) and human contributors working in this repository. Follows the
[agents.md](https://agents.md) convention.

## Project overview

A serverless AI **deep research agent** on AWS. Given a natural-language
research query it decomposes the question into facet-labeled sub-queries,
dispatches parallel researcher sub-agents armed with MCP tools, verifies
the findings, and synthesizes a cited report.

The system is mid-pivot from a single-container AgentCore Runtime agent
to a leaner **two-Lambda + FastMCP** topology. The target architecture
lives in [`docs/design/README.md`](docs/design/README.md). The committed
CDK + agent code still implements the previous design and is being
migrated incrementally.

Built on the **Strands Agents SDK** with **Amazon Bedrock**.

Default LLM: `us.anthropic.claude-opus-4-6-v1` (cross-region inference
profile). Default region: `us-west-2`.

## Repository layout

```
app/
  preprocess/         Pre-processing Lambda — Step 1/2/3/1g of the
                      local aws-deep-research skill (intent, strategy,
                      decomposition, slug, research contract). Active.
  agent/runtime/      Existing container agent on AgentCore Runtime
                      (deterministic Python workflow). Will be replaced
                      by an Agent Lambda in a follow-up PR.
  agent/invoker/      API Gateway → Lambda async-invoke trampoline.
  agent/{ws,status}/  WebSocket + status handlers.
  mcp-servers/        Five Lambda MCP servers (fetch, brave, github,
                      aws-docs, feeds). Will be consolidated into a
                      single FastMCP server on AgentCore Runtime.
  frontend/           React + Vite SPA.
infra/
  bin/app.ts          CDK entry point.
  lib/*.ts            Six CDK stacks (Data, McpServers, Api, AgentRuntime,
                      Frontend, Observability).
  config/index.ts     Per-stage configuration (region, model id, budgets).
docs/design/          Target architecture, decisions, learnings.
scripts/              End-to-end test + utility scripts.
```

## Setup commands

### CDK infrastructure

```bash
cd infra
npm ci                          # install deps
npx tsc --noEmit                # type-check CDK code
npx cdk synth --quiet           # synthesize all stacks
npx cdk diff                    # show pending changes
npx cdk deploy --all --require-approval broadening
npx cdk watch DeepResearch-McpServers-dev   # hot-deploy MCP Lambdas
```

Per-stack deploys: `npm run deploy:data`, `:mcp`, `:api`, `:agent`,
`:frontend`.

### Pre-processing Lambda (local)

```bash
cd app/preprocess
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PREPROCESS_LOCAL=1 AWS_REGION=us-west-2 \
  .venv/bin/python test_local.py "<your query>"
# or run the bundled fixtures
PREPROCESS_LOCAL=1 AWS_REGION=us-west-2 \
  .venv/bin/python test_local.py --fixtures
```

`PREPROCESS_LOCAL=1` skips S3 / DynamoDB / downstream Lambda invokes and
writes artifacts to `app/preprocess/.preprocess-out/<slug>/` instead.

### Agent Runtime (local)

```bash
cd app/agent/runtime
pip install -r requirements.txt
python main.py                       # serves on :8080
curl http://localhost:8080/ping      # health check
```

### Frontend

```bash
cd app/frontend
npm ci && npm run build              # outputs to dist/
```

### End-to-end test (post-deploy)

```bash
./scripts/post-deploy-test.sh                  # full setup + test
./scripts/post-deploy-test.sh --skip-setup
./scripts/post-deploy-test.sh --query "..." --depth quick
```

## Code style

- **Python 3.13+** for all Lambda + agent code. Type hints required on
  public functions. Format + lint with `ruff` when available
  (`ruff format <files> && ruff check --fix <files>`).
- **TypeScript** for CDK. No `any`. Stack props are typed interfaces.
  Format with `prettier` when available.
- **Conventional Commits** for all commit messages — see
  [Commit guidelines](#commit-guidelines).
- **Pydantic v2** models for any structured LLM output. Use closed
  `Literal` types where the value space is known.
- Lambda environment variables in `SCREAMING_SNAKE_CASE`; Python
  module-level constants likewise.

## Testing

- `app/preprocess/test_local.py` — runs the pre-processor against real
  Bedrock; six fixture queries cover every strategy and approval path.
- `npx cdk synth --quiet` is the minimum bar for CDK changes — must
  succeed for all stacks before commit.
- `./scripts/post-deploy-test.sh` is the e2e smoke test against a
  deployed stack.
- No unit-test framework is wired yet; favor running against real
  Bedrock with `PREPROCESS_LOCAL=1` until a fixtures-based suite lands.

## Architecture (current, mid-pivot)

The committed code implements a **deterministic Python workflow** that
calls bounded LLM agents at specific steps:

1. **Decomposer** — LLM (no tools) breaks query into 3–5 sub-questions.
2. **Researchers** — parallel sub-agents (≤3 concurrent) call MCP tools
   and write findings to S3.
3. **Verifier** — Python checks S3 file sizes (>500 bytes = OK).
4. **Synthesizer** — LLM (write-only tool) reads pre-assembled findings,
   writes the final report.
5. **Completion** — DynamoDB status update + WebSocket progress push.

DynamoDB key schema: `pk = "{userId}#{slug}"`,
`sk = "meta" | "status" | "cost"`.

Stack dependency order:

```
Data → McpServers → Api → AgentRuntime → Frontend
                                       → Observability
```

The **target** architecture (two-Lambda + FastMCP) is documented in
[`docs/design/README.md`](docs/design/README.md) and tracked in
follow-up PRs.

## Configuration

- Per-stage CDK config: `infra/config/index.ts` (region, model id, budget
  thresholds, alarm email).
- Default model: `us.anthropic.claude-opus-4-6-v1` (override via
  `BEDROCK_MODEL_ID` env var).
- Secrets: `prod/deepresearch/Search` in AWS Secrets Manager
  (`BRAVE_API_KEY`, `TAVILY_API_KEY`, `GITHUB_TOKEN`).

## Commit guidelines

**All git operations (commit, push, tag) run inside the
`my-git-workspace` Docker container** — never on the host.

```bash
# Verify the container is up
docker ps --filter name=my-git-workspace --format '{{.Names}}'
# Start it (from the parent docker-github directory) if needed
docker compose run -d --rm --name my-git-workspace git-workspace

# All git commands use this pattern
docker exec my-git-workspace git -C /workspace/repos/deep-research-cloud <command>
docker exec my-git-workspace gh <command>
```

Use **Conventional Commits**: `<type>(scope): <description>`

- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
  `chore`, `ci`. Append `!` (or a `BREAKING CHANGE:` footer) for
  backward-incompatible changes.
- Scope = folder/component name (e.g. `api`, `infra`, `agent`,
  `preprocess`, `frontend`, `mcp`). Omit for cross-cutting changes.
- Imperative mood ("add", not "added"). Subject ≤50 chars, no trailing
  period.
- Stage explicit file paths — never `git add .` or `git add -A`.
- Run linters (`ruff`, `prettier`) before staging when available.
- One concern per commit: split infra, app, and docs changes apart.

**Never commit** generated files (`node_modules/`, `__pycache__/`,
`.env*` except templates, `cdk.out/`, `.DS_Store`, `dist/`,
`app/preprocess/.venv/`, `app/preprocess/.preprocess-out/`).

**Never push without explicit human go-ahead** — agents must commit
locally and stop.

## Pull requests

- Open against `main`. Use `feat!` / `refactor!` titles for breaking
  changes and include a `BREAKING CHANGE:` footer in the merge commit.
- The PR body should list every commit, the test plan, and any
  follow-up work.
- CI (`.github/workflows/deploy.yml`) runs on every PR: TypeScript
  compile + `cdk synth` + `cdk diff`. Push to `main` triggers a full
  deploy via OIDC role assumption.

## Security

- No secrets in code or environment defaults. All credentials flow
  through AWS Secrets Manager.
- IAM follows least privilege — each Lambda gets only the actions and
  resources it needs.
- The `fetch-mcp` server enforces an SSRF blocklist (private IP ranges,
  cloud metadata endpoints).
- API requests authenticate via Cognito JWT — the Lambda Authorizer
  validates the token before any handler runs.
- The agent never reads research findings into the parent context; it
  verifies them via S3 metadata only (size/existence). Sub-agents own
  their context windows.

## Documentation MCP servers

When working in this repo, prefer authoritative documentation lookups
over recall:

- **AWS services / CDK / CloudFormation / IAM**: search the
  `aws-knowledge-mcp-server`.
- **Bedrock AgentCore (Runtime, Memory, Code Interpreter, Browser,
  Gateway, Observability, Identity)**: search the
  `bedrock-agentcore-mcp-server`.

Do not hallucinate AWS service details — search and cite.

## CI/CD

`.github/workflows/deploy.yml`:

- Pull requests: TypeScript compile, `cdk synth`, `cdk diff`.
- Push to `main`: builds the frontend and deploys all stacks via OIDC
  role assumption.

# Changelog

All notable changes to Deep Research Cloud are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/)

## [Unreleased]

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

### Not Yet Implemented
- AgentCore Runtime invocation from invoker Lambda (TODO in handler)
- Pattern 3 sub-agent parallel dispatch (single-agent loop only)
- Token/cost tracking and DDB cost ledger writes
- Checkpoint/resume mechanism for crash recovery
- WAF on API Gateway
- React SPA frontend
- CI/CD pipeline

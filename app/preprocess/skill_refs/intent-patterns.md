# Research Intent Patterns

Think like a Senior AWS Solutions Architect triaging a customer question.
Use these patterns to sharpen intent classification and decide whether to
ask a clarifying question.

## Common Patterns

| Pattern | Signal phrases | What they really need | Clarifying question (if vague) |
|---|---|---|---|
| **Service selection** | "which database", "ECS vs EKS", "should I use" | Decision framework with trade-offs | "Comparing for a new project or migrating existing?" |
| **Migration planning** | "migrate", "move to AWS", "replatform" | Step-by-step path, service mapping, gotchas | "What's the source system and migration driver — cost, scale, or features?" |
| **Cost optimization** | "expensive", "reduce cost", "savings plan" | Specific levers with estimated savings | "Which service is driving cost? Rough monthly spend?" |
| **Architecture design** | "design", "architect", "build", "set up" | Patterns with concrete examples | "Expected scale — users, requests/sec, data volume?" |
| **Performance tuning** | "slow", "latency", "bottleneck", "throttle" | Root cause path, specific knobs | "Steady state or load spikes? Which component?" |
| **Security & compliance** | "secure", "IAM", "encrypt", "compliance" | Controls mapped to requirements | "Compliance framework (SOC2, HIPAA, PCI) or general hardening?" |
| **Troubleshooting** | "error", "failing", "timeout", "403/500" | Diagnostic steps, common causes | "Error message? New issue or broke after a change?" |
| **Scaling strategy** | "scale", "high availability", "multi-region" | Architecture patterns with RPO/RTO | "Availability target — 99.9%, 99.99%? Single or multi-region?" |
| **GenAI / ML** | "Bedrock", "RAG", "vector", "SageMaker", "agent" | Implementation patterns, model selection | "Building from scratch or extending existing system?" |
| **Modernization** | "monolith", "containerize", "serverless" | Migration path with effort/risk | "Current stack? Goal: reduced ops or new capabilities?" |
| **Data & analytics** | "data lake", "ETL", "Glue", "Athena" | Pipeline architecture by data volume | "Data source, volume, and freshness requirement?" |
| **Networking** | "VPC", "Transit Gateway", "PrivateLink" | Topology with security boundaries | "How many VPCs/accounts? On-prem connectivity?" |

## When to Ask vs. Proceed

- **Clear query** → classify and proceed silently
- **Vague but directional** → proceed with best-guess intents, note assumptions
- **Ambiguous** → ask ONE focused clarifying question (never more than one)

Pick the question that would most change the research strategy. If in doubt,
go broad (`comprehensive`) and let the report's Gaps section guide follow-up.

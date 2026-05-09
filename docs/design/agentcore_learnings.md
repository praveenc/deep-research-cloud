# Amazon Bedrock AgentCore — Implementation Learnings

> Hard-won lessons from deploying Deep Research Cloud to AgentCore Runtime.
> Use this as a reference to avoid repeating these mistakes.

---

## 1. boto3 Service Names

| Purpose | Correct Service Name | Wrong (will crash) |
|---------|---------------------|-------------------|
| Invoke an agent runtime | `bedrock-agentcore` | `bedrock-agentcore-runtime` ❌ |
| Create/manage runtimes (control plane) | `bedrock-agentcore-control` | `bedrock-agentcore` for creation ❌ |
| Invoke Bedrock models directly | `bedrock-runtime` | — |

```python
# CORRECT
client = boto3.client('bedrock-agentcore')
response = client.invoke_agent_runtime(...)

# WRONG — crashes with UnknownServiceError
client = boto3.client('bedrock-agentcore-runtime')  # ❌ Does not exist
```

**Rule**: Always verify the service name exists in the Lambda runtime's boto3 version before deploying. Check valid names via: `boto3.Session().get_available_services()`

---

## 2. invoke_agent_runtime() API Signature

The correct invocation call:

```python
import boto3
import json

client = boto3.client('bedrock-agentcore', region_name='us-west-2')

response = client.invoke_agent_runtime(
    agentRuntimeArn='arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/myAgent-AbCdEfGh',
    runtimeSessionId='unique-session-id-must-be-33-plus-chars',  # REQUIRED, 33+ chars
    payload=json.dumps({"prompt": "..."}).encode('utf-8'),
    # Optional:
    # qualifier='DEFAULT',
    # runtimeUserId='user-123',
)
```

### Required Parameters

| Parameter | Type | Notes |
|-----------|------|-------|
| `agentRuntimeArn` | string | Full ARN (not just the ID) |
| `runtimeSessionId` | string | **Must be 33+ characters**. Use UUID v4 (36 chars). |
| `payload` | bytes | JSON-encoded, as bytes |

### Common Mistakes

- ❌ Using `agentRuntimeId` instead of `agentRuntimeArn`
- ❌ Using `body` instead of `payload`
- ❌ Using `endpointName` (not a valid parameter)
- ❌ Omitting `runtimeSessionId` (required, 33+ char minimum)
- ❌ Passing payload as string instead of bytes

### All Valid Parameters (from service model)

```
contentType, accept, mcpSessionId, runtimeSessionId, mcpProtocolVersion,
runtimeUserId, traceId, traceParent, traceState, baggage,
agentRuntimeArn, qualifier, accountId, payload
```

---

## 3. invoke_agent_runtime() is SYNCHRONOUS

**Critical**: `invoke_agent_runtime()` blocks until the agent completes. For agents that take minutes (research, multi-step workflows), you CANNOT call this from:
- A Lambda behind API Gateway (29s timeout)
- A Lambda with default timeout (15s)

### Solution: Async self-invocation pattern

```python
# API handler (fast path — returns 202 immediately)
lambda_client.invoke(
    FunctionName=context.function_name,
    InvocationType='Event',  # Async — returns immediately
    Payload=json.dumps({'action': 'invoke_agent', ...}).encode(),
)
return {'statusCode': 202, ...}

# Same Lambda, second invocation (async worker — can run up to 15 min)
def _handle_agent_invocation(event):
    client = boto3.client('bedrock-agentcore')
    response = client.invoke_agent_runtime(...)  # Blocks for minutes — OK here
```

**IAM requirement**: The Lambda needs `lambda:InvokeFunction` on itself. Use an explicit policy (not `grantInvoke(self)` which creates circular CDK dependencies).

---

## 4. Bedrock Model IDs vs Inference Profiles

**You cannot use raw model IDs for on-demand invocation.** You MUST use inference profiles.

| ❌ Won't Work | ✅ Correct |
|--------------|-----------|
| `anthropic.claude-sonnet-4-20250514` | `us.anthropic.claude-sonnet-4-6` |
| `anthropic.claude-sonnet-4-20250514-v1:0` | `us.anthropic.claude-sonnet-4-20250514-v1:0` |

### How to find valid inference profiles

```bash
aws bedrock list-inference-profiles --region us-west-2 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'sonnet')].{id:inferenceProfileId, name:inferenceProfileName}" \
  --output table
```

### IAM for cross-region inference profiles

Cross-region profiles (prefixed `us.` or `global.`) route to foundation models in **multiple regions**. Your IAM policy must allow `bedrock:InvokeModel*` on:

```json
{
  "Resource": [
    "arn:aws:bedrock:*::foundation-model/*",
    "arn:aws:bedrock:*:<account-id>:inference-profile/*"
  ]
}
```

❌ **Wrong**: Scoping to a single region like `arn:aws:bedrock:us-west-2::foundation-model/...` — the profile may route to us-east-1.

---

## 5. Secrets Manager Key Names

Always verify the **exact key names** in your Secrets Manager secret match what the code expects.

```bash
# Check actual key names
aws secretsmanager get-secret-value --secret-id prod/deepresearch/Search \
  --query 'SecretString' --output text | python3 -c "import json,sys; print(list(json.loads(sys.stdin.read()).keys()))"
```

In our case: the secret had `BRAVE_SEARCH_API_KEY` but the MCP server env var `SECRET_KEY_NAME` was set to `BRAVE_API_KEY`.

---

## 6. CDK IAM Policy — Duplicate SID Errors

CDK's `grant*()` helper methods (e.g., `bucket.grantReadWrite(role)`, `table.grantReadWriteData(role)`) add policy statements with auto-generated SIDs.

**If you also add manual `PolicyStatement` objects with explicit `sid:` properties, they can collide with CDK-generated SIDs.**

### Fix: Don't use explicit `sid` on statements that share a policy with `grant*()` calls

```typescript
// ❌ Can collide with grant* generated SIDs
executionRole.addToPolicy(new iam.PolicyStatement({
  sid: 'CloudWatchMetrics',  // ← May collide
  actions: ['cloudwatch:PutMetricData'],
  resources: ['*'],
}));

// ✅ Let CDK handle SID generation
executionRole.addToPolicy(new iam.PolicyStatement({
  // No sid — CDK assigns unique ID
  actions: ['cloudwatch:PutMetricData'],
  resources: ['*'],
}));
```

Also: avoid `fn.grantInvoke(role)` in a loop for multiple functions — use a single statement with all ARNs instead:

```typescript
// ✅ Single statement, no SID collisions
executionRole.addToPolicy(new iam.PolicyStatement({
  actions: ['lambda:InvokeFunction'],
  resources: Object.values(mcpFunctions).map(fn => fn.functionArn),
}));
```

---

## 7. CDK Circular Dependencies

`grantInvoke(self)` on a Lambda function creates a circular dependency in CloudFormation because the function's ARN is both the resource being granted and the grantee.

### Fix: Use explicit ARN string

```typescript
// ❌ Creates circular dependency
invokerHandler.grantInvoke(invokerHandler);

// ✅ Use explicit ARN (functionName is known at synth time)
invokerHandler.addToRolePolicy(new iam.PolicyStatement({
  actions: ['lambda:InvokeFunction'],
  resources: [`arn:aws:lambda:${this.region}:${this.account}:function:my-function-name`],
}));
```

---

## 8. AgentCore Runtime Execution Role Trust Policy

The execution role must trust `bedrock-agentcore.amazonaws.com`:

```typescript
const executionRole = new iam.Role(this, 'AgentExecutionRole', {
  assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
});
```

The role also needs these baseline permissions (from AWS docs):
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` on `/aws/bedrock-agentcore/runtimes/*`
- `xray:PutTraceSegments`, `xray:PutTelemetryRecords`
- `cloudwatch:PutMetricData` (namespace: `bedrock-agentcore`)
- `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` (for container pull)
- `ecr:GetAuthorizationToken` (resource: `*`)

---

## 9. Agent Loop Prevention (Strands SDK)

**The Strands `Agent` class does NOT have a `max_turns` or `max_iterations` constructor parameter.** Don't try to pass one — it will raise `TypeError: Agent.__init__() got an unexpected keyword argument 'max_turns'`.

### Actual `Agent` constructor parameters (relevant ones)

```python
Agent(
    model=...,
    messages=...,
    tools=[...],
    system_prompt="...",
    callback_handler=...,
    conversation_manager=...,
    hooks=[...],
    retry_strategy=...,
    # ... no max_turns, no max_iterations
)
```

### How to actually limit iteration

1. **Restrict the tool set** — fewer tools = less to loop on. The synthesizer should only have `write_to_s3` so once it writes, the model has nothing else to call and naturally returns `end_turn`.

2. **Directive system prompts** — explicitly tell the model when to stop:
   ```
   IMPORTANT: After writing the report, STOP. Do not make any more tool calls.
   ```

3. **Watchdog cancellation** — the SDK provides `agent.cancel()` for external termination:
   ```python
   import threading

   agent = Agent(...)

   def _watchdog():
       agent.cancel()

   watchdog = threading.Timer(240.0, _watchdog)  # 4 minutes
   watchdog.daemon = True
   watchdog.start()

   try:
       result = agent(prompt)
       watchdog.cancel()  # Cancel timer if completed normally
   except Exception:
       watchdog.cancel()
       raise
   ```

4. **Hooks** — register hooks to count iterations and call `agent.cancel()` programmatically.

### The deeper architectural lesson

The local `aws-deep-research` skill is **deterministic** — it's a markdown SKILL that Claude follows step by step, not an agent loop. There's no LLM-controlled "figure out when to stop" logic.

When translating a skill to a cloud agent, **don't give one LLM the entire workflow with all the tools and hope it terminates**. Instead, structure the orchestrator as:

```python
# Python code (deterministic) drives the workflow
sub_questions = step_1_decompose(query)        # bounded LLM call
results = step_2_dispatch_researchers(...)     # ThreadPoolExecutor of bounded LLM calls
verified = step_3_verify_findings(...)         # pure Python, no LLM
report = step_4_synthesize(...)                # bounded LLM call with all findings pre-assembled
```

Each step uses the LLM for what it's good at (text understanding/generation) within a bounded scope. Python handles control flow.

### Observed symptom

With a single LLM-controlled orchestrator + many tools, the agent writes findings to S3 successfully but then loops — calling `dispatch_sub_agents` repeatedly or hitting MCP tools long after the workflow should have ended. The model interprets gaps in findings as reasons to research more, with no hard stop.

---

## 10. Lambda Module-Level Client Initialization

**Never initialize a boto3 client at module level if the service might not exist in the Lambda's boto3 version.** The import will crash and the Lambda will fail on INIT, returning opaque 502 errors.

```python
# ❌ Crashes Lambda INIT if service doesn't exist
agentcore_client = boto3.client('bedrock-agentcore-runtime')

# ✅ Lazy initialization — fails gracefully at call time
_agentcore_client = None

def _get_agentcore_client():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client('bedrock-agentcore')
    return _agentcore_client
```

---

## 11. AgentCore Container Requirements

| Requirement | Value |
|-------------|-------|
| Platform | `linux/arm64` |
| Port | `8080` |
| Health check endpoint | `GET /ping` |
| Invocation endpoint | `POST /invocations` |
| SDK entry | `BedrockAgentCoreApp()` + `@app.entrypoint` |

The `bedrock-agentcore` Python SDK handles the HTTP server setup automatically.

---

## 12. Deployment Checklist (Post-CDK Deploy)

After `cdk deploy --all`, these manual wiring steps are needed:

1. **Set `AGENT_RUNTIME_ARN` on invoker Lambda** — CDK can't cross-reference it without creating a dependency cycle
2. **Verify Secrets Manager key names match env vars** (`SECRET_KEY_NAME`)
3. **Verify Bedrock model access** — inference profile must be enabled in account
4. **Enable `USER_PASSWORD_AUTH`** on Cognito client (for CLI testing)

---

## Quick Reference: Full Working Invocation Flow

```
Client → POST /research (API Gateway)
  → Invoker Lambda (fast: validate + DDB write + return 202)
    → Self-invoke async (InvocationType='Event')
      → Invoker Lambda (worker: blocks on AgentCore)
        → bedrock-agentcore.invoke_agent_runtime()
          → AgentCore Runtime container (POST /invocations)
            → Strands Agent orchestrator
              → MCP Lambda tools (via Lambda Direct Invoke)
              → Sub-agents (ThreadPoolExecutor, parallel)
              → S3 writes (findings, report)
              → DDB status updates
              → WebSocket progress push
```

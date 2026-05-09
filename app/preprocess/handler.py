"""
Pre-processing Lambda — Steps 1, 2, 3, 1g of the local aws-deep-research skill.

Triggered by `POST /research`. Single LLM call (Strands `agent.structured_output`)
produces the full research plan, then we:

  1. Render `research-contract.md` from the structured contract data
  2. Write the contract to S3 at `<slug>/research-contract.md`
  3. Write a tracking record to DynamoDB
  4. Emit a structured + human-readable CloudWatch log line
  5. Return the plan to the API caller

For SIMPLE queries we hand off to the Agent Lambda asynchronously here; for
COMPLEX queries we return `needs_approval=true` and the React SPA shows the
contract for the user to confirm before posting `/research/{slug}/start`.

Local test mode:
    Set PREPROCESS_LOCAL=1 to skip S3/DDB writes and write artifacts to
    `./.preprocess-out/<slug>/` instead. See `test_local.py`.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.config import Config as BotocoreConfig

# Strands imports are deferred to function-scope so module import works
# in local-test mode without the SDK being fully wired (the schema and
# contract rendering do not depend on Strands).

from models import ContractData, PreprocessResult
from prompts import build_system_prompt, USER_PROMPT_TEMPLATE


# ─── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("preprocess")
logger.setLevel(logging.INFO)


# ─── Environment ───────────────────────────────────────────────────────
LOCAL_MODE = os.environ.get("PREPROCESS_LOCAL", "0") == "1"
RESEARCH_BUCKET = os.environ.get("RESEARCH_BUCKET", "")
TRACKING_TABLE = os.environ.get("TRACKING_TABLE", "")
AGENT_LAMBDA_NAME = os.environ.get("AGENT_LAMBDA_NAME", "")
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
STAGE = os.environ.get("STAGE", "dev")


# ─── Lazy AWS clients ──────────────────────────────────────────────────
_s3 = None
_ddb = None
_lambda = None


def _s3_client():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3")
    return _s3


def _ddb_table():
    global _ddb
    if _ddb is None:
        import boto3
        _ddb = boto3.resource("dynamodb").Table(TRACKING_TABLE)
    return _ddb


def _lambda_client():
    global _lambda
    if _lambda is None:
        import boto3
        _lambda = boto3.client("lambda")
    return _lambda


# ─── Strands agent (cached across warm invocations) ────────────────────
_agent = None


def _get_agent():
    """Build a Strands Agent with no tools — pure structured output."""
    global _agent
    if _agent is None:
        from strands import Agent
        from strands.models.bedrock import BedrockModel

        boto_cfg = BotocoreConfig(
            connect_timeout=10,
            read_timeout=60,  # one structured-output call, no tool loops
            retries={"max_attempts": 2, "mode": "standard"},
        )
        model = BedrockModel(
            model_id=BEDROCK_MODEL_ID,
            region_name=AWS_REGION,
            boto_client_config=boto_cfg,
        )
        _agent = Agent(model=model, system_prompt=build_system_prompt())
    return _agent


# ─── Contract markdown renderer ────────────────────────────────────────
def render_contract_md(query: str, contract: ContractData) -> str:
    """Render the ContractData into the `research-contract.md` format
    defined in the local skill's research-contract-guide."""
    def _bullets(items: list[str]) -> str:
        if not items:
            return "- _(none)_"
        return "\n".join(f"- {x}" for x in items)

    base_labeling = (
        "- Data matching constraints → include as-is\n"
        "- Data for older/different versions → tag with ⚠️ and label the actual version\n"
        "- Data with no version attribution → tag with \"⚠️ version unspecified\""
    )
    extra = (
        "\n" + "\n".join(f"- {x}" for x in contract.extra_labeling_rules)
        if contract.extra_labeling_rules
        else ""
    )

    return (
        "# Research Contract\n\n"
        f"> Source query: {query}\n\n"
        "## Entity Constraints\n"
        f"**Include**:\n{_bullets(contract.entity_includes)}\n\n"
        f"**Exclude**:\n{_bullets(contract.entity_excludes)}\n\n"
        "## Temporal Constraints\n"
        f"{_bullets(contract.temporal_constraints)}\n\n"
        "## Factual Anchors\n"
        f"{_bullets(contract.factual_anchors)}\n\n"
        "## Labeling Rules\n"
        f"{base_labeling}{extra}\n"
    )


# ─── Decomposition print block (Step 3 transparency rule) ──────────────
def render_decomposition_block(plan: PreprocessResult) -> str:
    """The exact 'Dispatching <agent> with: [N] "..." (facet: ...)' block
    the local skill prints before any API credits are spent. Re-emitted
    here for the CloudWatch log so operators see the same view."""
    lines: list[str] = []
    pairs = [
        ("aws-mcp-researcher", plan.decomposition.aws_mcp_researcher),
        ("web-content-researcher", plan.decomposition.web_content_researcher),
        ("agentcore-researcher", plan.decomposition.agentcore_researcher),
        ("github-researcher", plan.decomposition.github_researcher),
    ]
    for agent, subqs in pairs:
        if not subqs:
            continue
        lines.append(f"Dispatching {agent} with:")
        for i, sq in enumerate(subqs, start=1):
            lines.append(f'  [{i}] "{sq.query}"   (facet: {sq.facet})')
    if not lines:
        lines.append("(no decomposition — feed-only strategy)")
    return "\n".join(lines)


# ─── Core orchestration ────────────────────────────────────────────────
def run_preprocess(query: str, *, user_id: str = "local") -> dict[str, Any]:
    """Single entry point used by both the Lambda handler and local tests."""
    if not query or not query.strip():
        raise ValueError("query is required and must be non-empty")

    t0 = time.time()
    agent = _get_agent()

    user_prompt = USER_PROMPT_TEMPLATE.format(query=query.strip())
    logger.info("preprocess.start | query=%r user=%s", query, user_id)

    plan: PreprocessResult = agent.structured_output(PreprocessResult, user_prompt)
    elapsed_ms = int((time.time() - t0) * 1000)

    contract_md = render_contract_md(query, plan.contract)
    contract_key = f"{plan.slug}/research-contract.md"

    # ── Persist artifacts ──────────────────────────────────────────────
    if LOCAL_MODE:
        out_dir = Path(".preprocess-out") / plan.slug
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "research-contract.md").write_text(contract_md, encoding="utf-8")
        (out_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        artifact_uri = f"file://{(out_dir / 'research-contract.md').resolve()}"
    else:
        if not RESEARCH_BUCKET:
            raise RuntimeError("RESEARCH_BUCKET env var is required outside local mode")
        _s3_client().put_object(
            Bucket=RESEARCH_BUCKET,
            Key=contract_key,
            Body=contract_md.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        # Persist the structured plan too, for the Agent Lambda to read
        _s3_client().put_object(
            Bucket=RESEARCH_BUCKET,
            Key=f"{plan.slug}/plan.json",
            Body=plan.model_dump_json().encode("utf-8"),
            ContentType="application/json",
        )
        artifact_uri = f"s3://{RESEARCH_BUCKET}/{contract_key}"

    # ── DynamoDB tracking record ───────────────────────────────────────
    if not LOCAL_MODE and TRACKING_TABLE:
        now = datetime.now(timezone.utc).isoformat()
        _ddb_table().put_item(
            Item={
                "pk": f"{user_id}#{plan.slug}",
                "sk": "meta",
                "stage": STAGE,
                "user_id": user_id,
                "slug": plan.slug,
                "original_query": query,
                "intents": plan.intents,
                "query_type": plan.query_type,
                "strategy": plan.strategy,
                "subagents": plan.subagents,
                "blog_categories": plan.blog_categories,
                "complexity": plan.complexity,
                "needs_approval": plan.needs_approval,
                "status": "pending_approval" if plan.needs_approval else "planned",
                "contract_uri": artifact_uri,
                "created_at": now,
                "updated_at": now,
            }
        )

    # ── CloudWatch log: structured (one line, queryable) ───────────────
    log_record = {
        "event": "preprocess.complete",
        "user_id": user_id,
        "slug": plan.slug,
        "query_type": plan.query_type,
        "intents": plan.intents,
        "strategy": plan.strategy,
        "subagents": plan.subagents,
        "blog_categories": plan.blog_categories,
        "complexity": plan.complexity,
        "needs_approval": plan.needs_approval,
        "subquery_counts": {
            "aws-mcp-researcher": len(plan.decomposition.aws_mcp_researcher),
            "web-content-researcher": len(plan.decomposition.web_content_researcher),
            "agentcore-researcher": len(plan.decomposition.agentcore_researcher),
            "github-researcher": len(plan.decomposition.github_researcher),
        },
        "elapsed_ms": elapsed_ms,
        "contract_uri": artifact_uri,
    }
    logger.info("preprocess.metrics %s", json.dumps(log_record, separators=(",", ":")))

    # ── CloudWatch log: human-readable decomposition (skill transparency rule) ─
    decomp_block = render_decomposition_block(plan)
    logger.info(
        "preprocess.decomposition slug=%s rationale=%r\n%s",
        plan.slug,
        plan.rationale,
        decomp_block,
    )

    # ── Hand off to Agent Lambda for SIMPLE queries ────────────────────
    if not plan.needs_approval and not LOCAL_MODE and AGENT_LAMBDA_NAME:
        _lambda_client().invoke(
            FunctionName=AGENT_LAMBDA_NAME,
            InvocationType="Event",
            Payload=json.dumps({
                "slug": plan.slug,
                "user_id": user_id,
                "source": "preprocess.auto-start",
            }).encode("utf-8"),
        )
        logger.info("preprocess.dispatched_agent slug=%s", plan.slug)

    # ── Build the response ─────────────────────────────────────────────
    return {
        "slug": plan.slug,
        "status": "pending_approval" if plan.needs_approval else "started",
        "query_type": plan.query_type,
        "intents": plan.intents,
        "strategy": plan.strategy,
        "subagents": plan.subagents,
        "blog_categories": plan.blog_categories,
        "complexity": plan.complexity,
        "needs_approval": plan.needs_approval,
        "decomposition": plan.decomposition.model_dump(),
        "rationale": plan.rationale,
        "contract_uri": artifact_uri,
        "contract_markdown": contract_md if plan.needs_approval else None,
        "elapsed_ms": elapsed_ms,
    }


# ─── Lambda entry point ────────────────────────────────────────────────
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway → Lambda proxy event handler."""
    request_id = getattr(context, "aws_request_id", str(uuid.uuid4()))
    logger.info("preprocess.invoke request_id=%s", request_id)

    try:
        body = event.get("body") or "{}"
        if isinstance(body, str):
            body = json.loads(body)
        query = (body.get("query") or "").strip()

        # Cognito JWT claims (set by the Lambda Authorizer)
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
        )
        user_id = claims.get("sub") or claims.get("cognito:username") or "anonymous"

        result = run_preprocess(query, user_id=user_id)
        status_code = 202 if result["status"] == "started" else 200
        return {
            "statusCode": status_code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }
    except ValueError as e:
        logger.warning("preprocess.bad_request request_id=%s err=%s", request_id, e)
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e), "request_id": request_id}),
        }
    except Exception as e:
        logger.exception("preprocess.error request_id=%s", request_id)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "error": "preprocessing failed",
                "detail": str(e),
                "request_id": request_id,
            }),
        }

"""
Deep Research Cloud — AgentCore Runtime Agent

Deterministic workflow orchestrator (matches local aws-deep-research skill):

  1. Parse request → generate contract
  2. Dispatch sub-agents in parallel (isolated context)
  3. Verify findings (check sizes)
  4. Dispatch synthesizer with pre-assembled context
  5. Mark complete

The orchestrator is NOT an LLM loop — it's Python code that calls LLM-powered
sub-agents at specific steps. The LLM only operates within each sub-agent's
bounded task. This prevents infinite loops and makes the workflow predictable.

Deployed to AgentCore Runtime using the bedrock-agentcore SDK.
Endpoints: POST /invocations (mandatory), GET /ping (mandatory)
Platform: linux/arm64, Port: 8080
"""
import os
import json
import logging
import concurrent.futures
from strands import Agent
from strands.models.bedrock import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from tools import (
    invoke_mcp_server,
    write_to_s3,
    read_from_s3,
    update_task_status,
    push_ws_progress,
)
from cost_tracker import init_tracker, get_tracker

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)

# Environment configuration
STAGE = os.environ.get('STAGE', 'dev')
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'us.anthropic.claude-sonnet-4-6')
RESEARCH_BUCKET = os.environ.get('RESEARCH_BUCKET', '')
TRACKING_TABLE = os.environ.get('TRACKING_TABLE', '')
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE', '')
WS_API_ENDPOINT = os.environ.get('WS_API_ENDPOINT', '')
MAX_PARALLEL_SUBAGENTS = int(os.environ.get('MAX_PARALLEL_SUBAGENTS', '3'))


# ─── System Prompts ──────────────────────────────────────────────────

DECOMPOSER_PROMPT = """You are a research query decomposer. Given a research query, break it into
3-5 targeted sub-questions that together will provide comprehensive coverage.

Return ONLY a JSON array of strings — each string is a sub-question.
No explanation, no markdown, just the JSON array.

Example:
["What are the core features of X?", "How does X compare to Y?", "What are best practices for X?"]
"""

RESEARCHER_PROMPT = """You are a focused research sub-agent. Your job is to thoroughly investigate
ONE specific sub-question using the available MCP tools.

Instructions:
- Search multiple sources for comprehensive coverage
- Use aws-docs-mcp for AWS-specific documentation
- Use brave-mcp for recent web articles and blog posts
- Use github-mcp for code examples and implementations
- Use feeds-mcp for recent AWS blog announcements
- Use fetch-mcp to read full content from promising URLs
- Be thorough: make 3-5 tool calls minimum
- Organize findings with clear headings and source citations

Return your findings as well-structured markdown with:
- Key facts and insights (bulleted)
- Source citations with URLs [N]
- Relevant code snippets if applicable

IMPORTANT: When you are done gathering information, call write_to_s3 with your
complete findings document, then STOP. Do not continue researching after writing.
"""

SYNTHESIZER_PROMPT = """You are the Research Synthesizer. You receive pre-assembled findings from
multiple research sub-agents and produce a single, cohesive report.

## Process

1. Read all provided findings
2. Deduplicate overlapping information across sources
3. Organize findings by TOPIC (not by source)
4. Assign citation numbers [N] to every factual claim
5. Write the final report using write_to_s3

## Report Format

# Research Report: <Descriptive Title>

**Date**: <today>
**Query**: <original query>

## Executive Summary
<2-3 paragraphs with citations [N]. Standalone value.>

## Detailed Findings
### <Topic Section 1>
<Organized by topic, not by source. Inline citations throughout.>

### <Topic Section 2>
...

## Recommendations
<3-5 actionable recommendations.>

## Gaps & Limitations
<What could NOT be found or was incomplete.>

## References
[1] [Title](https://url)
[2] [Title](https://url)

## Quality Standards
- Synthesize, don't concatenate. Use bridging sentences.
- Every factual claim needs a [N] citation.
- No fabrication — only include information from the findings.
- Target 2,500-6,000 words. Cut redundancy ruthlessly.
- Surface gaps honestly.

IMPORTANT: Write the report to S3 using write_to_s3, then STOP immediately.
Do NOT make any more tool calls after writing the report.
"""


# ─── Agent Factory ────────────────────────────────────────────────────

def _create_model():
    """Create a Bedrock model instance."""
    return BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=os.environ.get('AWS_REGION', 'us-west-2'),
    )


def _create_researcher_agent() -> Agent:
    """Create a sub-agent for focused research on a single sub-question."""
    return Agent(
        model=_create_model(),
        system_prompt=RESEARCHER_PROMPT,
        tools=[invoke_mcp_server, write_to_s3, read_from_s3],
        max_turns=12,
    )


def _create_synthesizer_agent() -> Agent:
    """Create the synthesizer agent — reads findings, writes one report."""
    return Agent(
        model=_create_model(),
        system_prompt=SYNTHESIZER_PROMPT,
        tools=[write_to_s3],
        max_turns=3,  # Read prompt + write report + stop. Never needs more.
    )


# ─── Deterministic Workflow Steps ─────────────────────────────────────

def step_1_decompose(query: str, depth: str) -> list[str]:
    """Decompose query into sub-questions using LLM."""
    agent = Agent(
        model=_create_model(),
        system_prompt=DECOMPOSER_PROMPT,
        tools=[],  # No tools — pure LLM generation
        max_turns=1,
    )

    num_questions = {'quick': 3, 'standard': 4, 'comprehensive': 5}.get(depth, 3)
    result = agent(f"Decompose this into {num_questions} sub-questions: {query}")

    # Parse JSON array from response
    response_text = str(result)
    try:
        # Find JSON array in response
        start = response_text.index('[')
        end = response_text.rindex(']') + 1
        sub_questions = json.loads(response_text[start:end])
        if isinstance(sub_questions, list) and all(isinstance(q, str) for q in sub_questions):
            return sub_questions
    except (ValueError, json.JSONDecodeError):
        pass

    # Fallback: split by newlines if JSON parsing fails
    logger.warning("Failed to parse decomposition as JSON, using fallback")
    return [query]  # Fall back to single question


def step_2_dispatch_researchers(slug: str, sub_questions: list[str], sources: list[str]) -> list[dict]:
    """Dispatch researcher sub-agents in parallel. Returns results with S3 keys."""
    results = []

    def run_researcher(index: int, question: str) -> dict:
        agent = _create_researcher_agent()
        findings_key = f"{slug}/findings/sub-{index:02d}.md"

        # Build source instruction
        source_tools = []
        if 'aws-docs' in sources:
            source_tools.append("aws-docs-mcp (search_aws_docs, get_aws_doc_page)")
        if 'web' in sources:
            source_tools.append("brave-mcp (web_search)")
            source_tools.append("fetch-mcp (fetch_url)")
        if 'github' in sources:
            source_tools.append("github-mcp (search_repositories, search_code, get_file_content)")
        if 'feeds' in sources:
            source_tools.append("feeds-mcp (get_feed)")

        prompt = f"""Research this sub-question thoroughly:

Sub-question: {question}

Available MCP tools: {', '.join(source_tools)}

Use invoke_mcp_server to call tools. Examples:
  invoke_mcp_server(server_name="brave-mcp", tool_name="web_search", arguments={{"query": "..."}})
  invoke_mcp_server(server_name="aws-docs-mcp", tool_name="search_aws_docs", arguments={{"query": "..."}})

Make 3-5 tool calls for thorough research.
Write your complete findings to S3 key: {findings_key}
"""
        try:
            agent(prompt)
            return {"index": index, "question": question, "status": "OK", "key": findings_key}
        except Exception as e:
            logger.error(f"Researcher {index} failed: {e}")
            return {"index": index, "question": question, "status": "FAILED", "error": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_SUBAGENTS) as executor:
        futures = {
            executor.submit(run_researcher, i, q): i
            for i, q in enumerate(sub_questions)
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result(timeout=300)
                results.append(result)
            except Exception as e:
                idx = futures[future]
                results.append({"index": idx, "question": sub_questions[idx], "status": "TIMEOUT", "error": str(e)})

    results.sort(key=lambda r: r["index"])
    return results


def step_3_verify_findings(slug: str, results: list[dict]) -> list[dict]:
    """Check each findings file exists and has content. Returns status list."""
    import boto3
    s3 = boto3.client('s3')

    verified = []
    for r in results:
        if r["status"] != "OK":
            verified.append({**r, "verified": "MISSING"})
            continue

        try:
            resp = s3.head_object(Bucket=RESEARCH_BUCKET, Key=r["key"])
            size = resp['ContentLength']
            if size < 500:
                verified.append({**r, "verified": "WEAK", "size": size})
            else:
                verified.append({**r, "verified": "OK", "size": size})
        except Exception:
            verified.append({**r, "verified": "MISSING"})

    return verified


def step_4_synthesize(slug: str, query: str, verified_results: list[dict]) -> str:
    """
    Dispatch synthesizer with ALL context pre-assembled.
    The synthesizer reads findings and writes one report. Single pass.
    """
    agent = _create_synthesizer_agent()
    report_key = f"{slug}/report.md"

    # Pre-read all findings and assemble into the prompt (like local skill)
    import boto3
    s3 = boto3.client('s3')

    findings_sections = []
    for r in verified_results:
        if r["verified"] == "OK":
            try:
                obj = s3.get_object(Bucket=RESEARCH_BUCKET, Key=r["key"])
                content = obj['Body'].read().decode('utf-8', errors='replace')
                # Truncate very large findings
                if len(content) > 30000:
                    content = content[:30000] + "\n\n[...TRUNCATED at 30KB...]"
                findings_sections.append(
                    f"## Findings: {r['question']}\n\n{content}"
                )
            except Exception as e:
                findings_sections.append(f"## {r['question']}: [READ ERROR: {e}]")
        elif r["verified"] == "WEAK":
            findings_sections.append(
                f"## {r['question']}: [WEAK — only {r.get('size', 0)} bytes, may indicate failure]"
            )
        else:
            findings_sections.append(
                f"## {r['question']}: [MISSING — sub-agent failed to produce output]"
            )

    combined_findings = "\n\n---\n\n".join(findings_sections)

    prompt = f"""Synthesize these research findings into a comprehensive report.

Original Query: {query}

{combined_findings}

Write the final report to S3 with key: {report_key}
"""

    try:
        agent(prompt)
        return report_key
    except Exception as e:
        logger.error(f"Synthesizer failed: {e}")
        return ""


# ─── AgentCore Runtime App ────────────────────────────────────────────

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    """
    Deterministic research workflow — no LLM-controlled loop.
    Each step is Python code calling bounded LLM agents.
    """
    query = payload.get('query', '')
    slug = payload.get('slug', '')
    user_id = payload.get('userId', 'anonymous')
    depth = payload.get('depth', 'standard')
    sources = payload.get('sources', ['aws-docs', 'web', 'github'])

    logger.info(f"Starting research: slug={slug}, depth={depth}, sources={sources}")
    tracker = init_tracker(slug=slug, user_id=user_id)

    try:
        # ─── Step 1: Decompose ────────────────────────────────────────
        push_ws_progress(user_id=user_id, slug=slug,
                         message="Decomposing query into sub-questions...",
                         step="decomposing", progress_pct=5)

        sub_questions = step_1_decompose(query, depth)
        logger.info(f"Decomposed into {len(sub_questions)} sub-questions")

        # Write contract to S3
        contract = {
            "query": query,
            "depth": depth,
            "sources": sources,
            "sub_questions": sub_questions,
        }
        write_to_s3(key=f"{slug}/contract.json",
                    content=json.dumps(contract, indent=2),
                    content_type="application/json")

        # ─── Step 2: Dispatch researchers ─────────────────────────────
        update_task_status(table_name=TRACKING_TABLE, user_id=user_id,
                          slug=slug, status="IN_PROGRESS")
        push_ws_progress(user_id=user_id, slug=slug,
                         message=f"Dispatching {len(sub_questions)} research agents...",
                         step="researching", progress_pct=15)

        results = step_2_dispatch_researchers(slug, sub_questions, sources)
        succeeded = sum(1 for r in results if r["status"] == "OK")
        logger.info(f"Research complete: {succeeded}/{len(sub_questions)} succeeded")

        push_ws_progress(user_id=user_id, slug=slug,
                         message=f"Research done: {succeeded}/{len(sub_questions)} agents succeeded",
                         step="researching", progress_pct=60)

        # ─── Step 3: Verify findings ─────────────────────────────────
        verified = step_3_verify_findings(slug, results)
        ok_count = sum(1 for v in verified if v["verified"] == "OK")

        if ok_count == 0:
            raise RuntimeError("All research sub-agents failed — no findings to synthesize")

        # ─── Step 4: Synthesize ───────────────────────────────────────
        push_ws_progress(user_id=user_id, slug=slug,
                         message="Synthesizing findings into report...",
                         step="synthesizing", progress_pct=70)

        report_key = step_4_synthesize(slug, query, verified)

        if not report_key:
            raise RuntimeError("Synthesizer failed to produce report")

        # ─── Step 5: Complete ─────────────────────────────────────────
        update_task_status(table_name=TRACKING_TABLE, user_id=user_id,
                          slug=slug, status="COMPLETE")
        push_ws_progress(user_id=user_id, slug=slug,
                         message="Research complete!",
                         step="complete", progress_pct=100)

        tracker.finalize()
        logger.info(f"Research complete: slug={slug}, cost={tracker.to_dict()}")

        return {
            "status": "COMPLETE",
            "slug": slug,
            "report_key": report_key,
            "cost": tracker.to_dict(),
        }

    except Exception as e:
        logger.error(f"Research failed: slug={slug}, error={e}")
        tracker.finalize()

        update_task_status(table_name=TRACKING_TABLE, user_id=user_id,
                          slug=slug, status="FAILED", error=str(e))
        push_ws_progress(user_id=user_id, slug=slug,
                         message=f"Research failed: {e}",
                         step="error", progress_pct=0)

        return {"status": "FAILED", "slug": slug, "error": str(e), "cost": tracker.to_dict()}


if __name__ == '__main__':
    app.run()

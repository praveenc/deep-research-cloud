"""
Sub-agent dispatch tools — Pattern 3 (Isolated Context).

Each sub-agent gets its own Strands Agent instance with a fresh conversation,
runs its research in isolation, and writes findings to S3. The orchestrator
dispatches them in parallel via ThreadPoolExecutor.
"""
import json
import os
import logging
import concurrent.futures
from strands import tool

logger = logging.getLogger(__name__)

RESEARCH_BUCKET = os.environ.get('RESEARCH_BUCKET', '')
MAX_PARALLEL_SUBAGENTS = int(os.environ.get('MAX_PARALLEL_SUBAGENTS', '3'))


def _run_single_researcher(sub_question: str, slug: str, index: int, sources: list[str]) -> dict:
    """
    Run a single researcher sub-agent in isolated context.

    Each call creates a fresh Agent instance — no shared conversation state.
    This prevents cross-contamination between parallel research threads.
    """
    from main import create_researcher_agent

    agent = create_researcher_agent()
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

    prompt = f"""Research this specific sub-question thoroughly:

Sub-question: {sub_question}

Available MCP tools: {', '.join(source_tools)}

After gathering findings, write the complete findings document to S3:
- Key: {findings_key}
- Format: Markdown with citations

Use invoke_mcp_server to call the MCP tools. Example:
  invoke_mcp_server(server_name="brave-mcp", tool_name="web_search", arguments={{"query": "..."}})
  invoke_mcp_server(server_name="aws-docs-mcp", tool_name="search_aws_docs", arguments={{"query": "..."}})

Make at least 3-5 tool calls for thorough research. Write findings to S3 when done.
"""

    try:
        result = agent(prompt)
        return {
            "index": index,
            "sub_question": sub_question,
            "status": "SUCCESS",
            "findings_key": findings_key,
            "summary": str(result)[:500],
        }
    except Exception as e:
        logger.error(f"Sub-agent {index} failed: {e}")
        return {
            "index": index,
            "sub_question": sub_question,
            "status": "FAILED",
            "error": str(e),
        }


@tool
def dispatch_sub_agents(
    slug: str,
    sub_questions: list[str],
    sources: list[str],
    user_id: str = "anonymous",
) -> str:
    """
    Dispatch parallel researcher sub-agents, one per sub-question.

    Each sub-agent runs in complete isolation (Pattern 3) — fresh Agent instance,
    no shared memory, no conversation leakage. They write findings to S3 independently.

    Args:
        slug: Research slug (S3 prefix)
        sub_questions: List of 3-5 targeted sub-questions to investigate in parallel
        sources: Available source types (aws-docs, web, github, feeds)
        user_id: User ID for WS progress updates

    Returns:
        JSON summary of all sub-agent results with their S3 findings keys.
    """
    from tools import push_ws_progress

    logger.info(f"Dispatching {len(sub_questions)} sub-agents for slug={slug}")

    # Push progress before starting parallel research
    push_ws_progress(
        user_id=user_id,
        slug=slug,
        message=f"Dispatching {len(sub_questions)} research agents in parallel...",
        step="researching",
        progress_pct=20,
    )

    results = []

    # Run sub-agents in parallel with controlled concurrency
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_SUBAGENTS) as executor:
        futures = {}
        for i, question in enumerate(sub_questions):
            future = executor.submit(
                _run_single_researcher,
                sub_question=question,
                slug=slug,
                index=i,
                sources=sources,
            )
            futures[future] = i

        # Collect results as they complete
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                result = future.result(timeout=300)  # 5-min timeout per sub-agent
                results.append(result)
                logger.info(f"Sub-agent {idx} completed: {result['status']}")
            except concurrent.futures.TimeoutError:
                results.append({
                    "index": idx,
                    "sub_question": sub_questions[idx],
                    "status": "TIMEOUT",
                    "error": "Sub-agent exceeded 5-minute timeout",
                })
            except Exception as e:
                results.append({
                    "index": idx,
                    "sub_question": sub_questions[idx],
                    "status": "FAILED",
                    "error": str(e),
                })

    # Sort by index for deterministic output
    results.sort(key=lambda r: r["index"])

    # Push progress
    succeeded = sum(1 for r in results if r["status"] == "SUCCESS")
    push_ws_progress(
        user_id=user_id,
        slug=slug,
        message=f"Research complete: {succeeded}/{len(sub_questions)} sub-agents succeeded",
        step="researching",
        progress_pct=60,
    )

    return json.dumps(results, indent=2)


@tool
def run_synthesizer(
    slug: str,
    query: str,
    sub_questions: list[str],
    findings_keys: list[str],
    user_id: str = "anonymous",
) -> str:
    """
    Run the synthesizer sub-agent to produce the final research report.

    Reads all findings from S3, combines them into a comprehensive report,
    and writes the final report back to S3.

    Args:
        slug: Research slug (S3 prefix)
        query: Original research query
        sub_questions: The decomposed sub-questions that were investigated
        findings_keys: S3 keys of the findings files to synthesize
        user_id: User ID for WS progress updates

    Returns:
        Confirmation of report generation with the S3 key.
    """
    from main import create_synthesizer_agent
    from tools import push_ws_progress, read_from_s3

    logger.info(f"Starting synthesis for slug={slug}")

    push_ws_progress(
        user_id=user_id,
        slug=slug,
        message="Synthesizing research findings into final report...",
        step="synthesizing",
        progress_pct=70,
    )

    # Read all findings
    all_findings = []
    for key in findings_keys:
        content = read_from_s3(key=key)
        if not content.startswith("NOT_FOUND") and not content.startswith("S3_READ_ERROR"):
            all_findings.append(f"## Findings from: {key}\n\n{content}")
        else:
            all_findings.append(f"## {key}: [No findings available]")

    combined_findings = "\n\n---\n\n".join(all_findings)
    report_key = f"{slug}/report.md"

    # Create synthesizer with isolated context
    synthesizer = create_synthesizer_agent()

    prompt = f"""Synthesize the following research findings into a comprehensive report.

Original Query: {query}

Sub-questions investigated:
{chr(10).join(f'  {i+1}. {q}' for i, q in enumerate(sub_questions))}

Research Findings:
{combined_findings}

Write the final report to S3 with key: {report_key}
The report should be comprehensive (3000-6000 words), well-structured, and properly cited.
"""

    try:
        result = synthesizer(prompt)

        push_ws_progress(
            user_id=user_id,
            slug=slug,
            message="Report synthesis complete!",
            step="synthesizing",
            progress_pct=90,
        )

        return json.dumps({
            "status": "SUCCESS",
            "report_key": report_key,
            "summary": str(result)[:500],
        })

    except Exception as e:
        logger.error(f"Synthesizer failed: {e}")
        return json.dumps({
            "status": "FAILED",
            "error": str(e),
        })

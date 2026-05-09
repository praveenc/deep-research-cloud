"""
Deep Research Cloud — AgentCore Runtime Agent

Single Strands agent that orchestrates the full research lifecycle:
1. Intent classification + strategy selection
2. Query decomposition + slug generation
3. Research contract → S3
4. Dispatch sub-agents IN PARALLEL (Pattern 3, isolated context)
5. Verify findings (check S3 file sizes)
6. Synthesizer sub-agent → writes report to S3
7. Visual-generator sub-agent → writes HTML to S3
8. Push "complete" via WebSocket

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
from cost_tracker import init_tracker, get_tracker, record_usage_callback

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger(__name__)

# Environment configuration
STAGE = os.environ.get('STAGE', 'dev')
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'anthropic.claude-sonnet-4-20250514')
RESEARCH_BUCKET = os.environ.get('RESEARCH_BUCKET', '')
TRACKING_TABLE = os.environ.get('TRACKING_TABLE', '')
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE', '')
WS_API_ENDPOINT = os.environ.get('WS_API_ENDPOINT', '')

# Sub-agent concurrency limit (avoid throttling Bedrock)
MAX_PARALLEL_SUBAGENTS = int(os.environ.get('MAX_PARALLEL_SUBAGENTS', '3'))


# ─── System Prompts ──────────────────────────────────────────────────

ORCHESTRATOR_PROMPT = """You are Deep Research Cloud — an expert research orchestrator that produces
comprehensive, well-cited research reports on AWS and cloud technology topics.

Your workflow is:
1. Push WS progress: "Starting research"
2. Classify the user's intent and select a research strategy
3. Decompose the query into 3-5 targeted sub-questions
4. Write the research contract (sub-questions + strategy) to S3: {slug}/contract.json
5. Update task status to IN_PROGRESS
6. Call `dispatch_sub_agents` with the list of sub-questions — this runs them IN PARALLEL
7. After all sub-agents complete, verify each findings file exists in S3
8. Call `run_synthesizer` to produce the final report
9. Update task status to COMPLETE
10. Push WS progress: "Research complete"

IMPORTANT RULES:
- Use `dispatch_sub_agents` for parallel research — do NOT manually invoke MCP servers one at a time
- Use `run_synthesizer` for the final report — it has isolated context to avoid prompt confusion
- Always cite sources with URLs
- Write all artifacts under the slug prefix in S3
- After step 10 (push "Research complete"), you are DONE. Do NOT make any more tool calls.
  Return a brief summary message and stop.
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
- Include direct quotes for key facts
- Note any conflicting information between sources

Return your findings as well-structured markdown with:
- Key facts and insights (bulleted)
- Source citations with URLs
- Relevant code snippets if applicable
- Gaps or areas needing further investigation
"""

SYNTHESIZER_PROMPT = """You are a research report synthesizer. You receive a collection of research
findings from multiple sub-agents and produce a single, cohesive, well-structured report.

Report format:
1. **Executive Summary** (2-3 paragraphs)
2. **Key Findings** (structured sections addressing the original query)
3. **Comparative Analysis** (if applicable — tables, pros/cons)
4. **Architecture/Implementation Notes** (if applicable)
5. **Recommendations** (actionable, prioritized)
6. **Sources** (numbered list of all cited URLs)

Guidelines:
- Cross-reference findings from different sources to verify claims
- Note where sources agree/disagree
- Use tables for comparisons
- Include code snippets where relevant
- Keep total length 3000-6000 words for comprehensive depth
- Every factual claim must have a [source] citation
"""


# ─── Agent Factory ────────────────────────────────────────────────────

def _create_model():
    """Create a Bedrock model instance."""
    return BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=os.environ.get('AWS_REGION', 'us-west-2'),
    )


def create_orchestrator_agent(slug: str) -> Agent:
    """Create the orchestrator agent with dispatch + synthesize tools."""
    from tools_subagent import dispatch_sub_agents, run_synthesizer

    model = _create_model()
    prompt = ORCHESTRATOR_PROMPT.replace('{slug}', slug)

    agent = Agent(
        model=model,
        system_prompt=prompt,
        tools=[
            invoke_mcp_server,
            write_to_s3,
            read_from_s3,
            update_task_status,
            push_ws_progress,
            dispatch_sub_agents,
            run_synthesizer,
        ],
        max_turns=30,  # Hard limit to prevent infinite loops
    )
    return agent


def create_researcher_agent() -> Agent:
    """Create a sub-agent for focused research on a single sub-question."""
    model = _create_model()
    agent = Agent(
        model=model,
        system_prompt=RESEARCHER_PROMPT,
        tools=[
            invoke_mcp_server,
            write_to_s3,
            read_from_s3,
        ],
        max_turns=15,  # Sub-agents should finish faster
    )
    return agent


def create_synthesizer_agent() -> Agent:
    """Create a sub-agent for report synthesis."""
    model = _create_model()
    agent = Agent(
        model=model,
        system_prompt=SYNTHESIZER_PROMPT,
        tools=[
            write_to_s3,
            read_from_s3,
        ],
        max_turns=10,  # Synthesis should be quick — mostly one big write
    )
    return agent


# ─── AgentCore Runtime App (SDK Integration) ─────────────────────────

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    """
    Process incoming research requests from the invoker Lambda.

    AgentCore Runtime calls this via POST /invocations with the payload
    from invoke_agent_runtime().
    """
    # Parse the research request
    query = payload.get('query', '')
    slug = payload.get('slug', '')
    user_id = payload.get('userId', 'anonymous')
    depth = payload.get('depth', 'standard')
    sources = payload.get('sources', ['aws-docs', 'web', 'github'])

    logger.info(f"Starting research: slug={slug}, depth={depth}, sources={sources}")

    # Compose the research prompt for the orchestrator
    research_prompt = f"""Research the following query with {depth} depth:

Query: {query}

Configuration:
- Slug: {slug}
- User ID: {user_id}
- Depth: {depth}
- Sources to use: {', '.join(sources)}
- S3 prefix: s3://{RESEARCH_BUCKET}/{slug}/
- Tracking table: {TRACKING_TABLE}

Execute the full research workflow now. Start by pushing a WS progress update,
then decompose the query and dispatch sub-agents in parallel.
"""

    # Initialize cost tracking for this research run
    tracker = init_tracker(slug=slug, user_id=user_id)

    # Execute the research via the orchestrator agent
    orchestrator = create_orchestrator_agent(slug)

    try:
        result = orchestrator(research_prompt)
        logger.info(f"Research complete: slug={slug}")

        # Finalize cost tracking (flush to DDB + CloudWatch)
        tracker.finalize()
        logger.info(f"Cost summary: {tracker.to_dict()}")

        return {
            "status": "COMPLETE",
            "slug": slug,
            "message": str(result),
            "cost": tracker.to_dict(),
        }
    except Exception as e:
        logger.error(f"Research failed: slug={slug}, error={e}")

        # Still flush partial cost data
        tracker.finalize()

        # Update status to FAILED
        update_task_status(
            table_name=TRACKING_TABLE,
            user_id=user_id,
            slug=slug,
            status="FAILED",
            error=str(e),
        )
        push_ws_progress(
            user_id=user_id,
            slug=slug,
            message=f"Research failed: {e}",
            step="error",
            progress_pct=0,
        )
        return {"status": "FAILED", "slug": slug, "error": str(e), "cost": tracker.to_dict()}


if __name__ == '__main__':
    app.run()

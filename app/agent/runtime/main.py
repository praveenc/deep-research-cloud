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


# System prompt for the orchestrator agent
SYSTEM_PROMPT = """You are Deep Research Cloud — an expert research agent that produces comprehensive, 
well-cited research reports on AWS and cloud technology topics.

You orchestrate research by:
1. Classifying the user's intent and selecting a research strategy
2. Decomposing the query into targeted sub-questions
3. Dispatching specialized sub-agents to gather information in parallel
4. Verifying the completeness and quality of findings
5. Synthesizing findings into a structured, cited report
6. Generating visual artifacts (charts, diagrams) when appropriate

You have access to MCP tools for:
- AWS documentation search (aws-docs-mcp)
- Web search via Brave (brave-mcp)
- GitHub repository and code search (github-mcp)
- Blog/RSS feed extraction (feeds-mcp)
- General web content fetching (fetch-mcp)

Write all artifacts to S3 under the research slug prefix.
Push progress updates via WebSocket at each major step.
Track token usage for cost attribution.

Always cite sources with URLs. Be thorough but concise.
"""


def create_agent() -> Agent:
    """Create and configure the Strands agent with Bedrock model and tools."""
    model = BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=os.environ.get('AWS_REGION', 'us-west-2'),
    )

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            invoke_mcp_server,
            write_to_s3,
            read_from_s3,
            update_task_status,
            push_ws_progress,
        ],
    )

    return agent


# ─── AgentCore Runtime App (SDK Integration) ─────────────────────────
# The BedrockAgentCoreApp automatically creates:
# - POST /invocations endpoint (mandatory for AgentCore Runtime)
# - GET /ping endpoint (mandatory health check)
# - Runs on port 8080

app = BedrockAgentCoreApp()
agent = create_agent()


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

    # Compose the research prompt
    research_prompt = f"""Research the following query with {depth} depth:

Query: {query}

Configuration:
- Slug: {slug}
- User ID: {user_id}
- Depth: {depth}
- Sources to use: {', '.join(sources)}
- S3 prefix: s3://{RESEARCH_BUCKET}/{slug}/

Instructions:
1. First, push a WS progress update: "Starting research for: {query}"
2. Classify the intent and select a research strategy
3. Decompose into 3-5 targeted sub-questions
4. Write the research contract to S3: {slug}/contract.json
5. For each sub-question, use the appropriate MCP tools to gather information
6. Write findings to S3: {slug}/findings/<source>.md
7. Verify all findings files exist and have content
8. Synthesize into a final report: {slug}/report.md
9. Update task status to COMPLETE
10. Push final WS progress: "Research complete"
"""

    # Execute the research
    try:
        result = agent(research_prompt)
        logger.info(f"Research complete: slug={slug}")
        return {"status": "COMPLETE", "slug": slug, "message": str(result)}
    except Exception as e:
        logger.error(f"Research failed: slug={slug}, error={e}")
        # Update status to FAILED
        update_task_status(
            table_name=TRACKING_TABLE,
            user_id=user_id,
            slug=slug,
            status="FAILED",
            error=str(e),
        )
        return {"status": "FAILED", "slug": slug, "error": str(e)}


if __name__ == '__main__':
    app.run()

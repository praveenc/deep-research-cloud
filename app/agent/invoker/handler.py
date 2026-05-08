"""
Invoker Lambda — validates research request, creates DDB task record,
and async-invokes the AgentCore Runtime agent.

POST /research
{
  "query": "Compare SageMaker vs Bedrock for RAG workloads",
  "options": {
    "depth": "comprehensive",  // "quick" | "standard" | "comprehensive"
    "sources": ["aws-docs", "web", "github"]  // optional filter
  }
}
"""
import json
import os
import time
import uuid
import hashlib
import re
import boto3
from datetime import datetime, timezone

TRACKING_TABLE = os.environ['TRACKING_TABLE']
RESEARCH_BUCKET = os.environ['RESEARCH_BUCKET']
AGENT_RUNTIME_ID = os.environ.get('AGENT_RUNTIME_ID', '')
STAGE = os.environ.get('STAGE', 'dev')

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TRACKING_TABLE)
# AgentCore Runtime client — will use when GA
# agentcore_client = boto3.client('bedrock-agent-runtime')


def generate_slug(query: str) -> str:
    """Generate a URL-safe slug from the research query."""
    # Normalize and truncate
    slug = re.sub(r'[^a-z0-9\s-]', '', query.lower().strip())
    slug = re.sub(r'[\s-]+', '-', slug)[:60]
    # Append short hash for uniqueness
    hash_suffix = hashlib.sha256(f"{query}{time.time()}".encode()).hexdigest()[:8]
    return f"{slug}-{hash_suffix}"


def lambda_handler(event, context):
    """Handle POST /research — create task and invoke agent."""
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        query = body.get('query', '').strip()

        if not query or len(query) < 10:
            return _response(400, {'error': 'Query must be at least 10 characters'})

        if len(query) > 2000:
            return _response(400, {'error': 'Query must be under 2000 characters'})

        # Extract user ID from Cognito JWT claims
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        user_id = claims.get('sub', 'anonymous')
        email = claims.get('email', '')

        # Generate slug and task ID
        slug = generate_slug(query)
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        options = body.get('options', {})
        depth = options.get('depth', 'standard')
        sources = options.get('sources', ['aws-docs', 'web', 'github'])

        # Write task record to DynamoDB
        task_record = {
            'pk': f"{user_id}#{slug}",
            'sk': 'meta',
            'taskId': task_id,
            'slug': slug,
            'userId': user_id,
            'email': email,
            'query': query,
            'depth': depth,
            'sources': sources,
            'status': 'PENDING',
            'createdAt': now,
            'updatedAt': now,
            'ttl': int(time.time()) + (90 * 24 * 3600),  # 90-day TTL
        }
        table.put_item(Item=task_record)

        # Also write a status-index entry for active queries
        table.put_item(Item={
            'pk': f"{user_id}#{slug}",
            'sk': 'status',
            'status': 'PENDING',
            'createdAt': now,
            'slug': slug,
            'userId': user_id,
        })

        # TODO: Async invoke AgentCore Runtime
        # When AgentCore SDK stabilizes, this will call:
        # agentcore_client.invoke_agent_runtime(
        #     agentRuntimeId=AGENT_RUNTIME_ID,
        #     inputText=json.dumps({
        #         'query': query,
        #         'slug': slug,
        #         'userId': user_id,
        #         'depth': depth,
        #         'sources': sources,
        #     }),
        # )

        return _response(202, {
            'taskId': task_id,
            'slug': slug,
            'status': 'PENDING',
            'statusUrl': f'/research/{slug}/status',
            'message': 'Research task submitted. Connect to WebSocket for real-time progress.',
        })

    except json.JSONDecodeError:
        return _response(400, {'error': 'Invalid JSON body'})
    except Exception as e:
        print(f"Error: {e}")
        return _response(500, {'error': 'Internal server error'})


def _response(status_code: int, body: dict) -> dict:
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body),
    }

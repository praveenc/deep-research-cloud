"""
Invoker Lambda — validates research request, creates DDB task record,
and fires off the AgentCore Runtime invocation asynchronously.

POST /research
{
  "query": "Compare SageMaker vs Bedrock for RAG workloads",
  "options": {
    "depth": "comprehensive",
    "sources": ["aws-docs", "web", "github"]
  }
}

AgentCore invocation is synchronous (blocks until agent completes), so we
invoke ourselves asynchronously via Lambda's Event invocation type to handle
the long-running AgentCore call without blocking the API Gateway response.
"""
import json
import os
import time
import uuid
import hashlib
import re
import boto3
from botocore.config import Config
from datetime import datetime, timezone

TRACKING_TABLE = os.environ['TRACKING_TABLE']
RESEARCH_BUCKET = os.environ['RESEARCH_BUCKET']
STAGE = os.environ.get('STAGE', 'dev')

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TRACKING_TABLE)
lambda_client = boto3.client('lambda')

# Lazy-init: bedrock-agentcore client
_agentcore_client = None


def _get_agentcore_client():
    """
    Build a bedrock-agentcore client tuned for long-running invocations.

    invoke_agent_runtime() is synchronous and can block for many minutes
    while the agent works. The boto3 default 60s read_timeout will cause
    retries that spawn duplicate parallel invocations — we MUST disable
    them and bump the timeout.
    """
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client(
            'bedrock-agentcore',
            config=Config(
                connect_timeout=10,
                read_timeout=900,  # 15 min — longer than any research run
                retries={'max_attempts': 1, 'mode': 'standard'},  # No retries
            ),
        )
    return _agentcore_client


def generate_slug(query: str) -> str:
    """Generate a URL-safe slug from the research query."""
    slug = re.sub(r'[^a-z0-9\s-]', '', query.lower().strip())
    slug = re.sub(r'[\s-]+', '-', slug)[:60]
    hash_suffix = hashlib.sha256(f"{query}{time.time()}".encode()).hexdigest()[:8]
    return f"{slug}-{hash_suffix}"


def lambda_handler(event, context):
    """
    Dual-mode handler:
    - API Gateway event (has 'httpMethod'): validate request, write task, async self-invoke
    - Async event (has 'action': 'invoke_agent'): call AgentCore synchronously
    """

    # ─── Mode 2: Async worker — invoke AgentCore (long-running) ──────
    if event.get('action') == 'invoke_agent':
        return _handle_agent_invocation(event)

    # ─── Mode 1: API Gateway — fast path (validate + DDB + return 202) ─
    try:
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

        # Status index entry
        table.put_item(Item={
            'pk': f"{user_id}#{slug}",
            'sk': 'status',
            'status': 'PENDING',
            'createdAt': now,
            'slug': slug,
            'userId': user_id,
        })

        # ─── Async self-invoke for the long-running AgentCore call ────
        # InvocationType='Event' returns immediately (202) and Lambda
        # runs the handler again with the agent invocation payload.
        agent_runtime_arn = os.environ.get('AGENT_RUNTIME_ARN', '')

        if agent_runtime_arn:
            async_payload = json.dumps({
                'action': 'invoke_agent',
                'agentRuntimeArn': agent_runtime_arn,
                'runtimeSessionId': task_id,  # UUID = 36 chars (>33 required)
                'query': query,
                'slug': slug,
                'userId': user_id,
                'depth': depth,
                'sources': sources,
            })

            lambda_client.invoke(
                FunctionName=context.function_name,
                InvocationType='Event',  # Async — returns immediately
                Payload=async_payload.encode('utf-8'),
            )
        else:
            print(f"WARNING: AGENT_RUNTIME_ARN not set. Task {slug} will remain PENDING.")

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


def _handle_agent_invocation(event):
    """
    Async worker: invokes AgentCore Runtime synchronously.
    This runs in a separate Lambda invocation (InvocationType='Event')
    so it doesn't block the API Gateway response.
    """
    agent_runtime_arn = event['agentRuntimeArn']
    runtime_session_id = event['runtimeSessionId']
    slug = event['slug']
    user_id = event['userId']

    invoke_payload = json.dumps({
        'query': event['query'],
        'slug': slug,
        'userId': user_id,
        'depth': event['depth'],
        'sources': event['sources'],
    })

    try:
        print(f"Invoking AgentCore: arn={agent_runtime_arn}, session={runtime_session_id}")

        client = _get_agentcore_client()
        response = client.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            runtimeSessionId=runtime_session_id,
            payload=invoke_payload.encode('utf-8'),
        )

        # Read the response
        response_body = response.get('response', b'')
        if hasattr(response_body, 'read'):
            response_body = response_body.read()
        if isinstance(response_body, bytes):
            response_body = response_body.decode('utf-8', errors='replace')

        print(f"AgentCore completed: slug={slug}, response_len={len(response_body)}")
        return {'status': 'COMPLETE', 'slug': slug}

    except Exception as e:
        print(f"AgentCore invoke error: {e}")
        # Mark task as FAILED
        table.update_item(
            Key={'pk': f"{user_id}#{slug}", 'sk': 'meta'},
            UpdateExpression='SET #s = :s, #e = :e',
            ExpressionAttributeNames={'#s': 'status', '#e': 'error'},
            ExpressionAttributeValues={':s': 'FAILED', ':e': str(e)},
        )
        # Also update status index
        table.update_item(
            Key={'pk': f"{user_id}#{slug}", 'sk': 'status'},
            UpdateExpression='SET #s = :s',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'FAILED'},
        )
        return {'status': 'FAILED', 'slug': slug, 'error': str(e)}


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

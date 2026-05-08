"""
Agent tools — callable by the Strands agent during research execution.

These tools bridge the agent to AWS services:
- invoke_mcp_server: Call Lambda MCP servers
- write_to_s3 / read_from_s3: Research artifact I/O
- update_task_status: DynamoDB task state management
- push_ws_progress: Real-time WebSocket notifications
"""
import json
import os
import logging
import boto3
from strands import tool

logger = logging.getLogger(__name__)

# AWS clients (reused across invocations)
_lambda_client = None
_s3_client = None
_dynamodb = None
_apigw_client = None


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client('lambda')
    return _lambda_client


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3')
    return _s3_client


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource('dynamodb')
    return _dynamodb


def _get_apigw_client():
    global _apigw_client
    if _apigw_client is None:
        endpoint = os.environ.get('WS_API_ENDPOINT', '')
        _apigw_client = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=endpoint,
        )
    return _apigw_client


# MCP Function ARN mapping
MCP_ARNS = {
    'fetch-mcp': os.environ.get('MCP_FETCH_ARN', ''),
    'aws-docs-mcp': os.environ.get('MCP_AWS_DOCS_ARN', ''),
    'brave-mcp': os.environ.get('MCP_BRAVE_ARN', ''),
    'github-mcp': os.environ.get('MCP_GITHUB_ARN', ''),
    'feeds-mcp': os.environ.get('MCP_FEEDS_ARN', ''),
}


@tool
def invoke_mcp_server(server_name: str, tool_name: str, arguments: dict) -> str:
    """
    Invoke a Lambda MCP server tool via Direct Invoke.

    Args:
        server_name: MCP server to invoke (fetch-mcp, aws-docs-mcp, brave-mcp, github-mcp, feeds-mcp)
        tool_name: Tool name within the MCP server
        arguments: Tool arguments as a dictionary

    Returns:
        Tool result text content
    """
    function_arn = MCP_ARNS.get(server_name)
    if not function_arn:
        return f"ERROR: Unknown MCP server: {server_name}. Available: {list(MCP_ARNS.keys())}"

    # Build MCP JSON-RPC request
    mcp_request = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    try:
        client = _get_lambda_client()
        response = client.invoke(
            FunctionName=function_arn,
            InvocationType='RequestResponse',
            Payload=json.dumps(mcp_request).encode(),
        )

        payload = json.loads(response['Payload'].read())
        body = json.loads(payload.get('body', '{}'))
        result = body.get('result', {})
        content = result.get('content', [])

        # Extract text from content blocks
        texts = [c.get('text', '') for c in content if c.get('type') == 'text']
        return '\n'.join(texts) if texts else 'No content returned'

    except Exception as e:
        logger.error(f"MCP invoke error: server={server_name}, tool={tool_name}, error={e}")
        return f"MCP_INVOKE_ERROR: {e}"


@tool
def write_to_s3(key: str, content: str, content_type: str = "text/markdown") -> str:
    """
    Write content to the research S3 bucket.

    Args:
        key: S3 object key (e.g., "my-research-slug/findings/aws-docs.md")
        content: Content to write
        content_type: MIME type (default: text/markdown)

    Returns:
        Confirmation message with S3 URI
    """
    bucket = os.environ.get('RESEARCH_BUCKET', '')
    if not bucket:
        return "ERROR: RESEARCH_BUCKET not configured"

    try:
        client = _get_s3_client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content.encode('utf-8'),
            ContentType=content_type,
        )
        return f"Written to s3://{bucket}/{key} ({len(content)} bytes)"

    except Exception as e:
        logger.error(f"S3 write error: key={key}, error={e}")
        return f"S3_WRITE_ERROR: {e}"


@tool
def read_from_s3(key: str) -> str:
    """
    Read content from the research S3 bucket.

    Args:
        key: S3 object key to read

    Returns:
        File content as string
    """
    bucket = os.environ.get('RESEARCH_BUCKET', '')
    if not bucket:
        return "ERROR: RESEARCH_BUCKET not configured"

    try:
        client = _get_s3_client()
        response = client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8', errors='replace')
        # Truncate very large files
        if len(content) > 50000:
            content = content[:50000] + "\n\n[...TRUNCATED at 50KB...]"
        return content

    except client.exceptions.NoSuchKey:
        return f"NOT_FOUND: s3://{bucket}/{key}"
    except Exception as e:
        logger.error(f"S3 read error: key={key}, error={e}")
        return f"S3_READ_ERROR: {e}"


@tool
def update_task_status(table_name: str, user_id: str, slug: str, status: str, error: str = "") -> str:
    """
    Update research task status in DynamoDB.

    Args:
        table_name: DynamoDB table name (use TRACKING_TABLE env var value)
        user_id: User ID from the research request
        slug: Research slug identifier
        status: New status (PENDING, IN_PROGRESS, RESEARCHING, SYNTHESIZING, COMPLETE, FAILED)
        error: Error message if status is FAILED

    Returns:
        Confirmation message
    """
    from datetime import datetime, timezone

    if not table_name:
        table_name = os.environ.get('TRACKING_TABLE', '')

    try:
        dynamodb = _get_dynamodb()
        table = dynamodb.Table(table_name)

        now = datetime.now(timezone.utc).isoformat()
        update_expr = "SET #status = :status, updatedAt = :now"
        expr_values = {':status': status, ':now': now}
        expr_names = {'#status': 'status'}

        if error:
            update_expr += ", #error = :error"
            expr_values[':error'] = error
            expr_names['#error'] = 'error'

        table.update_item(
            Key={'pk': f"{user_id}#{slug}", 'sk': 'meta'},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            ExpressionAttributeNames=expr_names,
        )

        # Also update the status-index entry
        table.update_item(
            Key={'pk': f"{user_id}#{slug}", 'sk': 'status'},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeValues={':status': status},
            ExpressionAttributeNames={'#status': 'status'},
        )

        return f"Status updated: {slug} → {status}"

    except Exception as e:
        logger.error(f"DDB update error: slug={slug}, error={e}")
        return f"DDB_UPDATE_ERROR: {e}"


@tool
def push_ws_progress(user_id: str, slug: str, message: str, step: str = "", progress_pct: int = 0) -> str:
    """
    Push a progress update to the client via WebSocket.

    Args:
        user_id: User ID to send the notification to
        slug: Research slug identifier
        message: Human-readable progress message
        step: Current step identifier (e.g., "researching", "synthesizing")
        progress_pct: Progress percentage (0-100)

    Returns:
        Confirmation or error message
    """
    connections_table = os.environ.get('CONNECTIONS_TABLE', '')
    if not connections_table:
        return "SKIP: CONNECTIONS_TABLE not configured"

    try:
        dynamodb = _get_dynamodb()
        table = dynamodb.Table(connections_table)

        # Find all connections for this user
        response = table.query(
            IndexName='user-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('userId').eq(user_id),
        )

        connections = response.get('Items', [])
        if not connections:
            logger.info(f"No active WS connections for user {user_id}")
            return "No active connections — progress not pushed"

        # Build progress payload
        payload = json.dumps({
            'type': 'progress',
            'slug': slug,
            'message': message,
            'step': step,
            'progressPct': progress_pct,
        }).encode()

        client = _get_apigw_client()
        sent = 0
        for conn in connections:
            try:
                client.post_to_connection(
                    ConnectionId=conn['connectionId'],
                    Data=payload,
                )
                sent += 1
            except client.exceptions.GoneException:
                # Connection stale — remove from DDB
                table.delete_item(Key={'connectionId': conn['connectionId']})
            except Exception as e:
                logger.warning(f"WS send error: conn={conn['connectionId']}, error={e}")

        return f"Progress pushed to {sent} connection(s): {message}"

    except Exception as e:
        logger.error(f"WS progress error: user={user_id}, error={e}")
        return f"WS_PROGRESS_ERROR: {e}"

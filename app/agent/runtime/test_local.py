"""
Local test harness for the Deep Research agent.

Allows testing the full agent flow locally without deploying to AgentCore Runtime.
MCP server calls go to real Lambda functions (requires AWS credentials) or can be
mocked for fully offline testing.

Usage:
  # With real AWS Lambdas (requires deployed MCP stack):
  python test_local.py --query "What is Amazon Bedrock AgentCore?"

  # With mocked MCP responses (no AWS needed):
  python test_local.py --query "What is Amazon Bedrock AgentCore?" --mock-mcp

  # Quick smoke test (skips sub-agents, just tests orchestrator loop):
  python test_local.py --query "What is Bedrock?" --smoke
"""
import os
import sys
import json
import argparse
import tempfile
import logging
from unittest.mock import patch, MagicMock
from datetime import datetime

# Configure environment for local testing
os.environ.setdefault('STAGE', 'local')
os.environ.setdefault('AWS_REGION', 'us-west-2')
os.environ.setdefault('BEDROCK_MODEL_ID', 'anthropic.claude-sonnet-4-20250514')
os.environ.setdefault('RESEARCH_BUCKET', 'deep-research-local-test')
os.environ.setdefault('TRACKING_TABLE', 'deep-research-tracking-local')
os.environ.setdefault('CONNECTIONS_TABLE', 'deep-research-connections-local')
os.environ.setdefault('WS_API_ENDPOINT', 'https://localhost/dev')
os.environ.setdefault('MAX_PARALLEL_SUBAGENTS', '2')

# MCP ARNs (use deployed functions or mock)
os.environ.setdefault('MCP_FETCH_ARN', 'arn:aws:lambda:us-west-2:123456789:function:deep-research-fetch-mcp-dev')
os.environ.setdefault('MCP_AWS_DOCS_ARN', 'arn:aws:lambda:us-west-2:123456789:function:deep-research-aws-docs-mcp-dev')
os.environ.setdefault('MCP_BRAVE_ARN', 'arn:aws:lambda:us-west-2:123456789:function:deep-research-brave-mcp-dev')
os.environ.setdefault('MCP_GITHUB_ARN', 'arn:aws:lambda:us-west-2:123456789:function:deep-research-github-mcp-dev')
os.environ.setdefault('MCP_FEEDS_ARN', 'arn:aws:lambda:us-west-2:123456789:function:deep-research-feeds-mcp-dev')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('test_local')


# ─── Mock fixtures ────────────────────────────────────────────────────

MOCK_MCP_RESPONSES = {
    'brave-mcp': {
        'web_search': json.dumps([
            {"title": "Amazon Bedrock Overview", "url": "https://aws.amazon.com/bedrock/", "description": "Fully managed service for foundation models", "age": "2d"},
            {"title": "Bedrock AgentCore GA", "url": "https://aws.amazon.com/blogs/aws/agentcore-ga/", "description": "AgentCore now generally available", "age": "1w"},
        ]),
    },
    'aws-docs-mcp': {
        'search_aws_docs': json.dumps([
            {"title": "What is Amazon Bedrock?", "url": "https://docs.aws.amazon.com/bedrock/latest/userguide/what-is-bedrock.html", "snippet": "Amazon Bedrock is a fully managed service..."},
        ]),
        'get_aws_doc_page': "Amazon Bedrock is a fully managed service that offers a choice of high-performing foundation models...",
    },
    'github-mcp': {
        'search_repositories': json.dumps([
            {"full_name": "awslabs/strands-agents", "description": "SDK for building AI agents", "url": "https://github.com/awslabs/strands-agents", "stars": 500},
        ]),
        'search_code': json.dumps([]),
    },
    'feeds-mcp': {
        'get_feed': json.dumps([
            {"title": "Introducing Bedrock AgentCore", "url": "https://aws.amazon.com/blogs/aws/agentcore/", "description": "New runtime for AI agents", "published": "2025-05-01"},
        ]),
    },
    'fetch-mcp': {
        'fetch_url': "This is the fetched page content about Amazon Bedrock. It provides foundation model access...",
    },
}


class MockS3:
    """In-memory S3 mock for local testing."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType='text/plain'):
        self.objects[f"{Bucket}/{Key}"] = Body if isinstance(Body, str) else Body.decode()
        logger.info(f"  [S3] PUT s3://{Bucket}/{Key} ({len(self.objects[f'{Bucket}/{Key}'])} bytes)")

    def get_object(self, Bucket, Key):
        full_key = f"{Bucket}/{Key}"
        if full_key not in self.objects:
            raise self._no_such_key()
        content = self.objects[full_key]
        body = MagicMock()
        body.read.return_value = content.encode()
        return {'Body': body}

    def _no_such_key(self):
        exc = Exception("NoSuchKey")
        exc.__class__.__name__ = 'NoSuchKey'
        return exc


class MockDynamoDB:
    """In-memory DynamoDB mock for local testing."""

    def __init__(self):
        self.tables = {}

    def Table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name)
        return self.tables[name]


class MockTable:
    def __init__(self, name):
        self.name = name
        self.items = {}

    def put_item(self, Item):
        key = f"{Item.get('pk', '')}#{Item.get('sk', Item.get('connectionId', ''))}"
        self.items[key] = Item
        logger.info(f"  [DDB] PUT {self.name} → {key}")

    def get_item(self, Key):
        key = f"{Key.get('pk', '')}#{Key.get('sk', '')}"
        item = self.items.get(key)
        return {'Item': item} if item else {}

    def update_item(self, Key, **kwargs):
        key = f"{Key.get('pk', '')}#{Key.get('sk', '')}"
        logger.info(f"  [DDB] UPDATE {self.name} → {key}")

    def query(self, **kwargs):
        return {'Items': []}

    def delete_item(self, Key):
        pass


def mock_lambda_invoke(FunctionName, InvocationType, Payload):
    """Mock Lambda invocation — returns canned MCP responses."""
    request = json.loads(Payload)
    tool_name = request.get('params', {}).get('name', '')

    # Determine which MCP server from the function name
    server = None
    for s in MOCK_MCP_RESPONSES:
        if s.replace('-', '') in FunctionName.replace('-', ''):
            server = s
            break

    if server and tool_name in MOCK_MCP_RESPONSES.get(server, {}):
        content = MOCK_MCP_RESPONSES[server][tool_name]
        result = {
            'body': json.dumps({
                'jsonrpc': '2.0',
                'id': request.get('id'),
                'result': {'content': [{'type': 'text', 'text': content}]},
            })
        }
    else:
        result = {
            'body': json.dumps({
                'jsonrpc': '2.0',
                'id': request.get('id'),
                'result': {'content': [{'type': 'text', 'text': f'MOCK: No response for {server}/{tool_name}'}]},
            })
        }

    payload_mock = MagicMock()
    payload_mock.read.return_value = json.dumps(result).encode()
    return {'Payload': payload_mock}


def run_test(query: str, mock_mcp: bool = False, smoke: bool = False):
    """Run the agent locally with optional mocking."""
    slug = f"test-{datetime.now().strftime('%H%M%S')}"
    user_id = 'local-tester'

    logger.info(f"{'='*60}")
    logger.info(f"LOCAL TEST: {query}")
    logger.info(f"Slug: {slug} | Mock MCP: {mock_mcp} | Smoke: {smoke}")
    logger.info(f"{'='*60}")

    # Set up mocks
    mock_s3 = MockS3()
    mock_ddb = MockDynamoDB()

    patches = []

    if mock_mcp:
        # Mock the Lambda client for MCP invocations
        patches.append(patch('tools._get_lambda_client', return_value=MagicMock(invoke=mock_lambda_invoke)))

    # Always mock S3 and DDB for local testing
    patches.append(patch('tools._get_s3_client', return_value=mock_s3))
    patches.append(patch('tools._get_dynamodb', return_value=mock_ddb))
    patches.append(patch('tools._get_apigw_client', return_value=MagicMock()))
    patches.append(patch('cost_tracker._get_dynamodb', return_value=mock_ddb))
    patches.append(patch('cost_tracker._get_cw_client', return_value=MagicMock()))

    if smoke:
        os.environ['MAX_PARALLEL_SUBAGENTS'] = '1'

    # Apply patches and run
    for p in patches:
        p.start()

    try:
        from main import invoke

        payload = {
            'query': query,
            'slug': slug,
            'userId': user_id,
            'depth': 'quick' if smoke else 'standard',
            'sources': ['aws-docs', 'web'] if smoke else ['aws-docs', 'web', 'github', 'feeds'],
        }

        logger.info(f"\nInvoking agent with payload: {json.dumps(payload, indent=2)}\n")
        result = invoke(payload)

        logger.info(f"\n{'='*60}")
        logger.info(f"RESULT: {result.get('status')}")
        if result.get('cost'):
            logger.info(f"COST: {json.dumps(result['cost'], indent=2)}")
        logger.info(f"{'='*60}")

        # Print S3 artifacts
        if mock_s3.objects:
            logger.info(f"\nS3 Artifacts Written:")
            for key, content in mock_s3.objects.items():
                logger.info(f"  {key} ({len(content)} bytes)")
                if 'report.md' in key:
                    logger.info(f"\n{'─'*40}")
                    logger.info(content[:2000])
                    if len(content) > 2000:
                        logger.info(f"  ... [{len(content) - 2000} more bytes]")
                    logger.info(f"{'─'*40}")

        return result

    finally:
        for p in patches:
            p.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Local test harness for Deep Research agent')
    parser.add_argument('--query', '-q', required=True, help='Research query to test')
    parser.add_argument('--mock-mcp', action='store_true', help='Mock MCP Lambda calls (no AWS needed)')
    parser.add_argument('--smoke', action='store_true', help='Quick smoke test (reduced depth)')
    args = parser.parse_args()

    result = run_test(query=args.query, mock_mcp=args.mock_mcp, smoke=args.smoke)
    sys.exit(0 if result.get('status') == 'COMPLETE' else 1)

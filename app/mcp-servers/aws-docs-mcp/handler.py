"""
aws-docs-mcp: AWS documentation and pricing search.

Uses AWS APIs (IAM-authenticated) to search documentation and pricing.
No external API keys needed — uses the Lambda execution role.
"""
import json
import urllib.parse
from urllib.request import urlopen, Request
from urllib.error import URLError


# AWS documentation search endpoint (public)
AWS_DOCS_SEARCH_URL = "https://docs.aws.amazon.com/search/doc-search.html"


def lambda_handler(event, context):
    """MCP Server handler for AWS documentation search."""
    body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tools/list":
        return _success(body.get("id"), {
            "tools": [
                {
                    "name": "search_aws_docs",
                    "description": "Search AWS official documentation for a topic",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query for AWS docs"},
                            "service": {"type": "string", "description": "AWS service filter (e.g., 'bedrock', 'sagemaker')"},
                            "max_results": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "get_aws_doc_page",
                    "description": "Fetch and extract content from a specific AWS documentation URL",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "AWS documentation URL"},
                            "max_length": {"type": "integer", "default": 10000},
                        },
                        "required": ["url"],
                    },
                },
            ]
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "search_aws_docs":
            return _handle_search(body.get("id"), arguments)
        elif tool_name == "get_aws_doc_page":
            return _handle_get_page(body.get("id"), arguments)

    return {"statusCode": 400, "body": json.dumps({"error": "Unknown method"})}


def _handle_search(request_id, arguments):
    """Search AWS documentation."""
    query = arguments.get("query", "")
    service = arguments.get("service", "")
    max_results = arguments.get("max_results", 5)

    # Build search URL
    search_query = f"{service} {query}".strip() if service else query
    encoded_query = urllib.parse.quote(search_query)

    # Use the AWS docs search API
    search_url = f"https://docs.aws.amazon.com/search/doc-search.html?searchQuery={encoded_query}&is498=true&limit={max_results}"

    try:
        req = Request(search_url, headers={
            "User-Agent": "DeepResearch-AwsDocsMCP/1.0",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8", errors="replace")

        # Parse results
        try:
            data = json.loads(content)
            results = []
            for item in data.get("items", [])[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("href", ""),
                    "snippet": item.get("description", "")[:300],
                })
            result_text = json.dumps(results, indent=2)
        except json.JSONDecodeError:
            result_text = f"Search returned non-JSON response for: {search_query}"

        return _success(request_id, {"content": [{"type": "text", "text": result_text}]})

    except (URLError, TimeoutError) as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"SEARCH_ERROR: {e}"}]})


def _handle_get_page(request_id, arguments):
    """Fetch a specific AWS documentation page."""
    url = arguments.get("url", "")
    max_length = arguments.get("max_length", 10000)

    # Only allow AWS documentation domains
    allowed_domains = ["docs.aws.amazon.com", "aws.amazon.com", "repost.aws"]
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in allowed_domains:
        return _success(request_id, {
            "content": [{"type": "text", "text": f"BLOCKED: Only AWS documentation URLs allowed. Got: {parsed.hostname}"}]
        })

    try:
        req = Request(url, headers={"User-Agent": "DeepResearch-AwsDocsMCP/1.0"})
        with urlopen(req, timeout=25) as response:
            content = response.read().decode("utf-8", errors="replace")[:max_length]

        return _success(request_id, {"content": [{"type": "text", "text": content}]})

    except (URLError, TimeoutError) as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"FETCH_ERROR: {e}"}]})


def _success(request_id, result):
    return {
        "statusCode": 200,
        "body": json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
    }

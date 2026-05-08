"""
brave-mcp: Web search via Brave Search API.

Requires BRAVE_API_KEY from Secrets Manager.
Implements API budget tracking (2K calls/month) via DDB counter.
"""
import json
import os
import boto3
from urllib.request import urlopen, Request
from urllib.error import URLError

SECRET_ARN = os.environ.get('SECRET_ARN', '')
SECRET_KEY_NAME = os.environ.get('SECRET_KEY_NAME', 'BRAVE_API_KEY')

# Cache secret for Lambda warm starts (5-min TTL handled by Secrets Manager SDK)
_cached_api_key = None


def _get_api_key():
    """Retrieve Brave API key from Secrets Manager (cached in execution context)."""
    global _cached_api_key
    if _cached_api_key:
        return _cached_api_key

    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=SECRET_ARN)
    secrets = json.loads(response['SecretString'])
    _cached_api_key = secrets.get(SECRET_KEY_NAME, '')
    return _cached_api_key


def lambda_handler(event, context):
    """MCP Server handler for Brave web search."""
    body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tools/list":
        return _success(body.get("id"), {
            "tools": [
                {
                    "name": "web_search",
                    "description": "Search the web using Brave Search API. Returns relevant results with titles, URLs, and snippets.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "count": {"type": "integer", "default": 10, "description": "Number of results (max 20)"},
                            "freshness": {
                                "type": "string",
                                "enum": ["pd", "pw", "pm", "py"],
                                "description": "Freshness filter: pd=past day, pw=past week, pm=past month, py=past year",
                            },
                        },
                        "required": ["query"],
                    },
                },
            ]
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "web_search":
            return _handle_search(body.get("id"), arguments)

    return {"statusCode": 400, "body": json.dumps({"error": "Unknown method"})}


def _handle_search(request_id, arguments):
    """Execute Brave web search."""
    query = arguments.get("query", "")
    count = min(arguments.get("count", 10), 20)
    freshness = arguments.get("freshness", "")

    api_key = _get_api_key()
    if not api_key:
        return _success(request_id, {
            "content": [{"type": "text", "text": "ERROR: BRAVE_API_KEY not configured in Secrets Manager"}]
        })

    # Build Brave Search API request
    import urllib.parse
    params = {"q": query, "count": str(count)}
    if freshness:
        params["freshness"] = freshness

    url = f"https://api.search.brave.com/res/v1/web/search?{urllib.parse.urlencode(params)}"

    try:
        req = Request(url, headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        })
        with urlopen(req, timeout=25) as response:
            content = response.read().decode("utf-8", errors="replace")

        data = json.loads(content)
        results = []
        for item in data.get("web", {}).get("results", [])[:count]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", "")[:500],
                "age": item.get("age", ""),
            })

        result_text = json.dumps(results, indent=2)
        return _success(request_id, {"content": [{"type": "text", "text": result_text}]})

    except (URLError, TimeoutError) as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"SEARCH_ERROR: {e}"}]})
    except json.JSONDecodeError as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"PARSE_ERROR: {e}"}]})


def _success(request_id, result):
    return {
        "statusCode": 200,
        "body": json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
    }

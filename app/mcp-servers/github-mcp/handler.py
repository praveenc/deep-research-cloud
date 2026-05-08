"""
github-mcp: GitHub repository and code search.

Requires GITHUB_TOKEN from Secrets Manager.
Uses GitHub REST API v3 for repo search, code search, and file content retrieval.
"""
import json
import os
import boto3
from urllib.request import urlopen, Request
from urllib.error import URLError
import urllib.parse

SECRET_ARN = os.environ.get('SECRET_ARN', '')
SECRET_KEY_NAME = os.environ.get('SECRET_KEY_NAME', 'GITHUB_TOKEN')

_cached_token = None


def _get_token():
    """Retrieve GitHub token from Secrets Manager."""
    global _cached_token
    if _cached_token:
        return _cached_token

    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=SECRET_ARN)
    secrets = json.loads(response['SecretString'])
    _cached_token = secrets.get(SECRET_KEY_NAME, '')
    return _cached_token


def lambda_handler(event, context):
    """MCP Server handler for GitHub search."""
    body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tools/list":
        return _success(body.get("id"), {
            "tools": [
                {
                    "name": "search_repositories",
                    "description": "Search GitHub repositories by topic, language, or keyword",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query (supports GitHub search qualifiers)"},
                            "sort": {"type": "string", "enum": ["stars", "forks", "updated"], "default": "stars"},
                            "max_results": {"type": "integer", "default": 10},
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "search_code",
                    "description": "Search code across GitHub repositories",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Code search query"},
                            "language": {"type": "string", "description": "Filter by programming language"},
                            "max_results": {"type": "integer", "default": 10},
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "get_file_content",
                    "description": "Get the content of a file from a GitHub repository",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string", "description": "Repository owner"},
                            "repo": {"type": "string", "description": "Repository name"},
                            "path": {"type": "string", "description": "File path within the repository"},
                            "ref": {"type": "string", "description": "Branch or commit ref (default: main)"},
                        },
                        "required": ["owner", "repo", "path"],
                    },
                },
                {
                    "name": "get_repo_readme",
                    "description": "Get the README content of a GitHub repository",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string", "description": "Repository owner"},
                            "repo": {"type": "string", "description": "Repository name"},
                        },
                        "required": ["owner", "repo"],
                    },
                },
            ]
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "search_repositories":
            return _handle_repo_search(body.get("id"), arguments)
        elif tool_name == "search_code":
            return _handle_code_search(body.get("id"), arguments)
        elif tool_name == "get_file_content":
            return _handle_get_file(body.get("id"), arguments)
        elif tool_name == "get_repo_readme":
            return _handle_get_readme(body.get("id"), arguments)

    return {"statusCode": 400, "body": json.dumps({"error": "Unknown method"})}


def _github_request(url: str) -> dict:
    """Make authenticated GitHub API request."""
    token = _get_token()
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "DeepResearch-GitHubMCP/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    with urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def _handle_repo_search(request_id, arguments):
    """Search GitHub repositories."""
    query = arguments.get("query", "")
    sort = arguments.get("sort", "stars")
    max_results = min(arguments.get("max_results", 10), 30)

    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&sort={sort}&per_page={max_results}"

    try:
        data = _github_request(url)
        results = []
        for repo in data.get("items", [])[:max_results]:
            results.append({
                "full_name": repo.get("full_name"),
                "description": (repo.get("description") or "")[:200],
                "url": repo.get("html_url"),
                "stars": repo.get("stargazers_count"),
                "language": repo.get("language"),
                "updated_at": repo.get("updated_at"),
                "topics": repo.get("topics", [])[:5],
            })

        return _success(request_id, {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]})

    except Exception as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"GITHUB_ERROR: {e}"}]})


def _handle_code_search(request_id, arguments):
    """Search code on GitHub."""
    query = arguments.get("query", "")
    language = arguments.get("language", "")
    max_results = min(arguments.get("max_results", 10), 30)

    search_query = f"{query} language:{language}" if language else query
    url = f"https://api.github.com/search/code?q={urllib.parse.quote(search_query)}&per_page={max_results}"

    try:
        data = _github_request(url)
        results = []
        for item in data.get("items", [])[:max_results]:
            results.append({
                "name": item.get("name"),
                "path": item.get("path"),
                "repository": item.get("repository", {}).get("full_name"),
                "url": item.get("html_url"),
                "score": item.get("score"),
            })

        return _success(request_id, {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]})

    except Exception as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"GITHUB_ERROR: {e}"}]})


def _handle_get_file(request_id, arguments):
    """Get file content from a repository."""
    owner = arguments.get("owner", "")
    repo = arguments.get("repo", "")
    path = arguments.get("path", "")
    ref = arguments.get("ref", "main")

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"

    try:
        data = _github_request(url)
        import base64
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        # Truncate large files
        if len(content) > 15000:
            content = content[:15000] + "\n\n[...TRUNCATED...]"

        return _success(request_id, {"content": [{"type": "text", "text": content}]})

    except Exception as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"GITHUB_ERROR: {e}"}]})


def _handle_get_readme(request_id, arguments):
    """Get repository README."""
    owner = arguments.get("owner", "")
    repo = arguments.get("repo", "")

    url = f"https://api.github.com/repos/{owner}/{repo}/readme"

    try:
        data = _github_request(url)
        import base64
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        if len(content) > 15000:
            content = content[:15000] + "\n\n[...TRUNCATED...]"

        return _success(request_id, {"content": [{"type": "text", "text": content}]})

    except Exception as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"GITHUB_ERROR: {e}"}]})


def _success(request_id, result):
    return {
        "statusCode": 200,
        "body": json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
    }

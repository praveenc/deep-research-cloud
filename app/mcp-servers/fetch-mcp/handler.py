"""
fetch-mcp: Web content extraction with SSRF protection.

Fetches web pages and extracts readable content. Implements URL blocklist
to prevent Server-Side Request Forgery (SSRF) attacks via indirect prompt injection.
"""
import json
import ipaddress
import urllib.parse
from urllib.request import urlopen, Request
from urllib.error import URLError

# SSRF Protection: Block private/internal IP ranges and metadata endpoints
BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),   # AWS metadata + ECS credentials
    ipaddress.ip_network("10.0.0.0/8"),        # Private class A
    ipaddress.ip_network("172.16.0.0/12"),     # Private class B
    ipaddress.ip_network("192.168.0.0/16"),    # Private class C
    ipaddress.ip_network("127.0.0.0/8"),       # Localhost
    ipaddress.ip_network("0.0.0.0/8"),         # Unspecified
    ipaddress.ip_network("fc00::/7"),          # IPv6 private
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]

BLOCKED_DOMAINS = [
    "metadata.google.internal",
    "metadata.internal",
]

ALLOWED_SCHEMES = ["https", "http"]  # http allowed for some blog feeds


def is_url_safe(url: str) -> tuple[bool, str]:
    """Validate URL against SSRF blocklist."""
    try:
        parsed = urllib.parse.urlparse(url)

        # Check scheme
        if parsed.scheme not in ALLOWED_SCHEMES:
            return False, f"Blocked scheme: {parsed.scheme}"

        # Check blocked domains
        hostname = parsed.hostname or ""
        if hostname in BLOCKED_DOMAINS:
            return False, f"Blocked domain: {hostname}"

        # Resolve and check IP
        import socket
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(hostname))
            for network in BLOCKED_NETWORKS:
                if ip in network:
                    return False, f"Blocked IP range: {ip} in {network}"
        except (socket.gaierror, ValueError):
            pass  # DNS resolution failed — allow (will fail at fetch time)

        return True, "OK"
    except Exception as e:
        return False, f"URL validation error: {e}"


def lambda_handler(event, context):
    """MCP Server handler for web content extraction."""
    # Parse MCP JSON-RPC request
    body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event

    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tools/list":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "tools": [
                        {
                            "name": "fetch_url",
                            "description": "Fetch and extract readable content from a URL",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string", "description": "URL to fetch"},
                                    "max_length": {"type": "integer", "default": 8000},
                                },
                                "required": ["url"],
                            },
                        }
                    ]
                },
            }),
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "fetch_url":
            url = arguments.get("url", "")
            max_length = arguments.get("max_length", 8000)

            # SSRF check
            safe, reason = is_url_safe(url)
            if not safe:
                return {
                    "statusCode": 200,
                    "body": json.dumps({
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "result": {"content": [{"type": "text", "text": f"BLOCKED: {reason}"}]},
                    }),
                }

            try:
                req = Request(url, headers={"User-Agent": "DeepResearch-FetchMCP/1.0"})
                with urlopen(req, timeout=25) as response:
                    content = response.read().decode("utf-8", errors="replace")[:max_length]

                return {
                    "statusCode": 200,
                    "body": json.dumps({
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "result": {"content": [{"type": "text", "text": content}]},
                    }),
                }
            except (URLError, TimeoutError) as e:
                return {
                    "statusCode": 200,
                    "body": json.dumps({
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "result": {"content": [{"type": "text", "text": f"FETCH_ERROR: {e}"}]},
                    }),
                }

    return {"statusCode": 400, "body": json.dumps({"error": "Unknown method"})}

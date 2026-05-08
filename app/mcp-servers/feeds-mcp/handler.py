"""
feeds-mcp: Blog and RSS feed extraction.

Fetches and parses RSS/Atom feeds from AWS blogs and other tech sources.
No external API keys needed — public RSS feeds.
"""
import json
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError
import urllib.parse
import re

# Pre-configured AWS blog feed URLs
AWS_BLOG_FEEDS = {
    "aws-news": "https://aws.amazon.com/blogs/aws/feed/",
    "machine-learning": "https://aws.amazon.com/blogs/machine-learning/feed/",
    "compute": "https://aws.amazon.com/blogs/compute/feed/",
    "architecture": "https://aws.amazon.com/blogs/architecture/feed/",
    "database": "https://aws.amazon.com/blogs/database/feed/",
    "security": "https://aws.amazon.com/blogs/security/feed/",
    "devops": "https://aws.amazon.com/blogs/devops/feed/",
    "containers": "https://aws.amazon.com/blogs/containers/feed/",
    "networking": "https://aws.amazon.com/blogs/networking-and-content-delivery/feed/",
    "storage": "https://aws.amazon.com/blogs/storage/feed/",
}


def lambda_handler(event, context):
    """MCP Server handler for blog/RSS feed extraction."""
    body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "tools/list":
        return _success(body.get("id"), {
            "tools": [
                {
                    "name": "list_feeds",
                    "description": "List available pre-configured AWS blog feeds",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                    },
                },
                {
                    "name": "get_feed",
                    "description": "Fetch and parse an RSS/Atom feed. Use feed_id for pre-configured AWS feeds or provide a custom URL.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "feed_id": {
                                "type": "string",
                                "description": f"Pre-configured feed ID: {', '.join(AWS_BLOG_FEEDS.keys())}",
                            },
                            "url": {"type": "string", "description": "Custom RSS/Atom feed URL"},
                            "max_items": {"type": "integer", "default": 10},
                            "search": {"type": "string", "description": "Filter items by keyword in title/description"},
                        },
                    },
                },
            ]
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "list_feeds":
            feeds_info = [{"id": k, "url": v} for k, v in AWS_BLOG_FEEDS.items()]
            return _success(body.get("id"), {
                "content": [{"type": "text", "text": json.dumps(feeds_info, indent=2)}]
            })
        elif tool_name == "get_feed":
            return _handle_get_feed(body.get("id"), arguments)

    return {"statusCode": 400, "body": json.dumps({"error": "Unknown method"})}


def _handle_get_feed(request_id, arguments):
    """Fetch and parse an RSS/Atom feed."""
    feed_id = arguments.get("feed_id", "")
    url = arguments.get("url", "")
    max_items = min(arguments.get("max_items", 10), 50)
    search = arguments.get("search", "").lower()

    # Resolve URL
    if feed_id and feed_id in AWS_BLOG_FEEDS:
        url = AWS_BLOG_FEEDS[feed_id]
    elif not url:
        return _success(request_id, {
            "content": [{"type": "text", "text": f"ERROR: Provide feed_id ({', '.join(AWS_BLOG_FEEDS.keys())}) or a custom URL"}]
        })

    # Basic URL validation
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return _success(request_id, {
            "content": [{"type": "text", "text": "ERROR: Only http/https URLs allowed"}]
        })

    try:
        req = Request(url, headers={"User-Agent": "DeepResearch-FeedsMCP/1.0"})
        with urlopen(req, timeout=25) as response:
            content = response.read().decode("utf-8", errors="replace")

        # Parse XML
        root = ET.fromstring(content)
        items = []

        # Handle RSS 2.0
        for item in root.findall('.//item'):
            entry = _parse_rss_item(item)
            if search and search not in entry.get('title', '').lower() and search not in entry.get('description', '').lower():
                continue
            items.append(entry)
            if len(items) >= max_items:
                break

        # Handle Atom if no RSS items found
        if not items:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry_elem in root.findall('.//atom:entry', ns):
                entry = _parse_atom_entry(entry_elem, ns)
                if search and search not in entry.get('title', '').lower() and search not in entry.get('description', '').lower():
                    continue
                items.append(entry)
                if len(items) >= max_items:
                    break

        result_text = json.dumps(items, indent=2)
        return _success(request_id, {"content": [{"type": "text", "text": result_text}]})

    except ET.ParseError as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"XML_PARSE_ERROR: {e}"}]})
    except (URLError, TimeoutError) as e:
        return _success(request_id, {"content": [{"type": "text", "text": f"FETCH_ERROR: {e}"}]})


def _parse_rss_item(item) -> dict:
    """Parse an RSS 2.0 <item> element."""
    title = item.findtext('title', '')
    link = item.findtext('link', '')
    description = item.findtext('description', '')
    pub_date = item.findtext('pubDate', '')

    # Strip HTML from description
    description = re.sub(r'<[^>]+>', '', description)[:500]

    return {
        "title": title,
        "url": link,
        "description": description,
        "published": pub_date,
    }


def _parse_atom_entry(entry, ns) -> dict:
    """Parse an Atom <entry> element."""
    title = entry.findtext('atom:title', '', ns)
    link_elem = entry.find('atom:link[@rel="alternate"]', ns)
    if link_elem is None:
        link_elem = entry.find('atom:link', ns)
    link = link_elem.get('href', '') if link_elem is not None else ''
    summary = entry.findtext('atom:summary', '', ns) or entry.findtext('atom:content', '', ns)
    published = entry.findtext('atom:published', '', ns) or entry.findtext('atom:updated', '', ns)

    summary = re.sub(r'<[^>]+>', '', summary)[:500]

    return {
        "title": title,
        "url": link,
        "description": summary,
        "published": published,
    }


def _success(request_id, result):
    return {
        "statusCode": 200,
        "body": json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}),
    }

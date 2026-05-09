"""
Local runner for the Pre-processing Lambda.

Runs `run_preprocess()` against AWS Bedrock with PREPROCESS_LOCAL=1, which
skips S3 / DynamoDB / Lambda invokes and writes artifacts to
`./.preprocess-out/<slug>/` instead.

Usage:
    cd app/preprocess
    pip install -r requirements.txt
    PREPROCESS_LOCAL=1 AWS_REGION=us-west-2 \\
        python test_local.py "Compare Bedrock Claude Sonnet 4.6 vs Haiku 4.5 for RAG"

Or run the built-in fixture set:
    PREPROCESS_LOCAL=1 AWS_REGION=us-west-2 python test_local.py --fixtures
"""

from __future__ import annotations
import json
import os
import sys

# Force local mode before importing the handler
os.environ.setdefault("PREPROCESS_LOCAL", "1")

from handler import run_preprocess  # noqa: E402


FIXTURES = [
    # Simple AWS query — should be docs-only or comprehensive, no approval gate
    "How does DynamoDB handle hot partitions?",
    # Complex multi-entity comparison — should require approval
    "Compare Claude Sonnet 4.6 vs Haiku 4.5 vs Llama 3.3 70B on Bedrock for "
    "enterprise RAG: pricing, latency, and quality benchmarks",
    # AgentCore-specific
    "How do I deploy a Strands Agent to Bedrock AgentCore Runtime?",
    # Generic / non-AWS
    "Circuit breaker patterns in distributed systems",
    # News / feed-only
    "What did AWS launch in machine learning last week?",
]


def _print_result(query: str, result: dict) -> None:
    print("=" * 78)
    print(f"QUERY: {query}")
    print("=" * 78)
    print(f"slug              : {result['slug']}")
    print(f"status            : {result['status']}")
    print(f"query_type        : {result['query_type']}")
    print(f"intents           : {result['intents']}")
    print(f"strategy          : {result['strategy']}")
    print(f"subagents         : {result['subagents']}")
    print(f"blog_categories   : {result['blog_categories']}")
    print(f"complexity        : {result['complexity']} (needs_approval={result['needs_approval']})")
    print(f"rationale         : {result['rationale']}")
    print(f"contract_uri      : {result['contract_uri']}")
    print(f"elapsed_ms        : {result['elapsed_ms']}")
    print("\n--- decomposition ---")
    print(json.dumps(result["decomposition"], indent=2))
    if result.get("contract_markdown"):
        print("\n--- research-contract.md ---")
        print(result["contract_markdown"])
    print()


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    queries: list[str]
    if args[0] == "--fixtures":
        queries = FIXTURES
    else:
        queries = [" ".join(args)]

    for q in queries:
        try:
            result = run_preprocess(q, user_id="local-test")
            _print_result(q, result)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED on query={q!r}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

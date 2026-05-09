"""
Pydantic models for the Pre-processing Lambda.

These mirror Steps 1, 2, 3, and 1g of the local aws-deep-research skill:

    Step 1  Analyze intent + strategy + query type (aws | generic)
    Step 1c Blog category selection (if web-content-researcher dispatched)
    Step 1g Research contract (entity / temporal / factual / labeling)
    Step 2  Generate slug (4–7 tokens, 30–60 chars, kebab-case)
    Step 3  Decompose into 2–3 facet-labeled subqueries per source
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator


def _none_to_list(v):
    """Coerce None -> [] so models that emit null for empty optional lists pass validation."""
    return [] if v is None else v


# ── Step 1a: 12 intents (drives default subagent set) ──────────────────
Intent = Literal[
    "service-overview",
    "architecture",
    "pricing",
    "comparison",
    "troubleshooting",
    "best-practices",
    "migration",
    "security-compliance",
    "cost-optimization",
    "agentcore",
    "code-examples",
    "news-updates",
]

# ── Step 1b: AWS vs generic (binary, changes everything) ───────────────
QueryType = Literal["aws", "generic"]

# ── Step 1d: Strategy modifies the intent default set ──────────────────
Strategy = Literal["feed-only", "docs-only", "pricing-focused", "comprehensive"]

# ── Step 4: Available subagents in the cloud agent runtime ─────────────
Subagent = Literal[
    "aws-mcp-researcher",
    "web-content-researcher",
    "agentcore-researcher",
    "github-researcher",
]

# ── Step 1c: Blog feed categories (≤3) ─────────────────────────────────
BlogCategory = Literal[
    "whatsnew",
    "machinelearning",
    "security",
    "bigdata",
    "databases",
    "containers",
    "serverless",
    "operations",
    "opensource",
]


class Subquery(BaseModel):
    """A single facet-labeled subquery for one researcher subagent."""
    query: str = Field(min_length=3, max_length=240)
    facet: str = Field(
        min_length=2,
        max_length=40,
        description="Short kebab-case label, e.g. 'pricing', 'features', 'limits'.",
    )


class Decomposition(BaseModel):
    """Step 3 — facet-labeled subqueries grouped by destination subagent.

    Only include lists for subagents that are actually being dispatched.
    Skip entirely for `feed-only` strategy.
    """
    aws_mcp_researcher: list[Subquery] = []
    web_content_researcher: list[Subquery] = []
    agentcore_researcher: list[Subquery] = []
    github_researcher: list[Subquery] = []

    _coerce_lists = field_validator(
        "aws_mcp_researcher",
        "web_content_researcher",
        "agentcore_researcher",
        "github_researcher",
        mode="before",
    )(_none_to_list)


class ContractData(BaseModel):
    """Step 1g — fields needed to build research-contract.md."""
    entity_includes: list[str] = Field(
        default_factory=list,
        description="Specific services / models / versions to research.",
    )
    entity_excludes: list[str] = Field(
        default_factory=list,
        description="Older versions, competing services, out-of-scope topics.",
    )
    temporal_constraints: list[str] = Field(
        default_factory=list,
        description="Time period, recency, 'current pricing only', etc.",
    )
    factual_anchors: list[str] = Field(
        default_factory=list,
        description="Key facts that must be verified before inclusion.",
    )
    extra_labeling_rules: list[str] = Field(
        default_factory=list,
        description="Additional ⚠️-style labeling rules beyond the defaults.",
    )

    _coerce_lists = field_validator(
        "entity_includes",
        "entity_excludes",
        "temporal_constraints",
        "factual_anchors",
        "extra_labeling_rules",
        mode="before",
    )(_none_to_list)


class PreprocessResult(BaseModel):
    """The full structured output of the Pre-processing Lambda.

    Drives every downstream decision in the Agent Lambda:
      - which subagents to spawn
      - what queries each subagent runs
      - what the synthesizer cross-validates against
      - whether the user gets a contract approval gate
    """
    # Step 1
    intents: list[Intent] = Field(min_length=1, max_length=3)
    query_type: QueryType
    strategy: Strategy
    subagents: list[Subagent] = Field(min_length=0, max_length=4)

    # Step 1c
    blog_categories: list[BlogCategory] = Field(default_factory=list, max_length=3)

    # Step 1e
    direct_urls: list[str] = Field(default_factory=list)

    # Step 2
    slug: str

    # Step 3
    decomposition: Decomposition

    # Step 1g
    contract: ContractData

    # Approval gate (Step 1g rule: complex => ask user; simple => proceed)
    complexity: Literal["simple", "complex"]
    needs_approval: bool

    # One-line rationale for the CloudWatch log
    rationale: str = Field(min_length=10, max_length=400)

    _coerce_lists = field_validator(
        "intents",
        "subagents",
        "blog_categories",
        "direct_urls",
        mode="before",
    )(_none_to_list)

    # ── Validators that mirror the skill's hard rules ──────────────────

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        # 4–7 hyphen-separated tokens, 30–60 chars, lowercase + digits + '-' only
        if not v or not v.replace("-", "").replace("0", "").isalnum() and not all(
            c.islower() or c.isdigit() or c == "-" for c in v
        ):
            raise ValueError(f"slug must be lowercase alnum + hyphens only: {v!r}")
        if not all(c.islower() or c.isdigit() or c == "-" for c in v):
            raise ValueError(f"slug must be lowercase alnum + hyphens only: {v!r}")
        if v.startswith("-") or v.endswith("-") or "--" in v:
            raise ValueError(f"slug has bad hyphen placement: {v!r}")
        tokens = v.split("-")
        if not (4 <= len(tokens) <= 7):
            raise ValueError(f"slug must be 4–7 tokens, got {len(tokens)}: {v!r}")
        if not (30 <= len(v) <= 60):
            raise ValueError(f"slug must be 30–60 chars, got {len(v)}: {v!r}")
        return v

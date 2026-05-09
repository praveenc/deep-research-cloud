# Research Contract Guide

The Research Contract is a lightweight artifact extracted from the query in
Step 1e. It captures hard facts, entity constraints, and version requirements
that every subagent must respect when gathering and reporting data.

## Purpose

Web sources frequently mix data across versions, services, and time periods.
The contract acts as a **shared ground truth** that travels through the entire
pipeline — researchers use it to tag/filter data, and the synthesizer uses it
to cross-validate before including any data point in the report.

## Contract Format

Write the contract as markdown to `<output-dir>/research-contract.md`.

```markdown
# Research Contract

## Entity Constraints
- **Include**: [specific services, models, versions to research]
- **Exclude**: [older versions, competing services, out-of-scope topics]

## Temporal Constraints
- [Time period, recency requirements, "current pricing only", etc.]

## Factual Anchors
- [Key facts that must be verified before inclusion]
- [Version-specific requirements for data points]

## Labeling Rules
- Data matching constraints → include as-is
- Data for older/different versions → tag with ⚠️ and label the actual version
- Data with no version attribution → tag with "⚠️ version unspecified"
```

## Examples

### Complex Query (model comparison)

Query: "Compare Claude Opus 4.6 vs Sonnet 4.6 vs Haiku 4.5 pricing and quality"

```markdown
# Research Contract

## Entity Constraints
- **Include**: Claude Opus 4.6, Claude Sonnet 4.6, Claude Haiku 4.5,
  Amazon Bedrock pricing
- **Exclude**: Claude 3.x, Claude 3.5.x, Claude 4.0, non-Bedrock pricing

## Temporal Constraints
- Pricing: 2026 current rates only (verify at aws.amazon.com/bedrock/pricing/)
- Benchmarks: must reference the exact model version (4.6 or 4.5)

## Factual Anchors
- Pricing data MUST be for the exact model versions above
- If only older version pricing is available, label as "⚠️ proxy data (Claude 3.5)"
- Never mix version-specific numbers in the same table without clear labels

## Labeling Rules
- ✅ "Claude Sonnet 4.6 scores 79.6% on SWE-bench"
- ⚠️ "Claude 3.5 Sonnet costs $3.00/1M input tokens (older version — current 4.6 pricing may differ)"
- ⚠️ "~5x cheaper than Opus (estimated from historical tier ratios — verify current pricing)"
- ❌ Do NOT present Claude 3.5 pricing as if it applies to Claude 4.6
- ❌ Do NOT present unsourced cost ratios or derived calculations without a ⚠️ label
```

### Simple Query (service overview)

Query: "How does Amazon S3 work?"

```markdown
# Research Contract

## Entity Constraints
- **Include**: Amazon S3, S3 storage classes, S3 features
- **Exclude**: Azure Blob Storage, Google Cloud Storage, MinIO
  (unless explicitly compared)

## Temporal Constraints
- Prefer current documentation and features (2025-2026)

## Factual Anchors
- Focus on S3 capabilities, not third-party alternatives
- If mentioning alternatives, clearly label as comparison context

## Labeling Rules
- Standard labeling — no special version constraints
```

### Recent Feature Query

Query: "Amazon S3 Files NFS support launched April 7, 2026"

```markdown
# Research Contract

## Entity Constraints
- **Include**: Amazon S3 Files, NFS v4.1/v4.2, S3 file system access
- **Exclude**: EFS (unless comparing), third-party NFS solutions

## Temporal Constraints
- Feature launched 2026-04-07 — prioritize launch content
- Pricing: current S3 Files pricing only

## Factual Anchors
- S3 Files is a NEW feature — pre-launch content won't have details
- Compare with EFS only when the query asks for comparison

## Labeling Rules
- Standard labeling — flag any pre-launch speculation as unverified
```

## When to Ask the User to Validate

**Ask** when the contract has 3+ entity constraints OR version-specific
requirements. Show the contract summary and ask:
> "I've extracted these key constraints from your query. Anything to add or correct?"

**Proceed silently** when the contract is simple (1-2 entities, no version
constraints). Just write the contract and move on.

## How Subagents Use the Contract

### Researchers
- Read `research-contract.md` at the start of their task
- When writing output, tag data points that don't match entity/version constraints
- Use entity exclusions to sharpen search queries (NOT operators)
- Never silently include version-mismatched data without a label

### Synthesizer
- Read `research-contract.md` before reading any research files
- Cross-validate every pricing table, benchmark score, and comparison
  against the contract's entity and temporal constraints
- If data doesn't match: include with explicit ⚠️ label and version note
- Add a "Data Accuracy Notes" subsection if any proxy/older-version data
  was used in the report

"""
Token & cost tracking — hooks into Strands agent callbacks to capture
Bedrock token usage and emit both DDB cost records and CloudWatch metrics.

Pricing (Claude Sonnet 4, us-west-2, as of 2025-05):
  Input:  $3.00 / 1M tokens
  Output: $15.00 / 1M tokens
  Cache read: $0.30 / 1M tokens
  Cache write: $3.75 / 1M tokens
"""
import os
import json
import time
import logging
import threading
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)

# Pricing per million tokens (USD)
PRICING = {
    'input': 3.00,
    'output': 15.00,
    'cache_read': 0.30,
    'cache_write': 3.75,
}

TRACKING_TABLE = os.environ.get('TRACKING_TABLE', '')
CW_NAMESPACE = 'DeepResearch'

_cw_client = None
_dynamodb = None


def _get_cw_client():
    global _cw_client
    if _cw_client is None:
        _cw_client = boto3.client('cloudwatch')
    return _cw_client


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource('dynamodb')
    return _dynamodb


class CostTracker:
    """
    Thread-safe accumulator for token usage across all agent invocations
    within a single research run.
    """

    def __init__(self, slug: str, user_id: str):
        self.slug = slug
        self.user_id = user_id
        self._lock = threading.Lock()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.invocation_count = 0
        self.start_time = time.time()

    def record_usage(self, usage: dict):
        """
        Record token usage from a single Bedrock invocation.

        Expected usage dict (from Strands callback or Bedrock response metadata):
        {
            "inputTokens": 1234,
            "outputTokens": 567,
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        }
        """
        with self._lock:
            self.input_tokens += usage.get('inputTokens', 0)
            self.output_tokens += usage.get('outputTokens', 0)
            self.cache_read_tokens += usage.get('cacheReadInputTokens', 0)
            self.cache_write_tokens += usage.get('cacheWriteInputTokens', 0)
            self.invocation_count += 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Calculate estimated cost based on token pricing."""
        cost = (
            (self.input_tokens / 1_000_000) * PRICING['input']
            + (self.output_tokens / 1_000_000) * PRICING['output']
            + (self.cache_read_tokens / 1_000_000) * PRICING['cache_read']
            + (self.cache_write_tokens / 1_000_000) * PRICING['cache_write']
        )
        return round(cost, 6)

    @property
    def duration_seconds(self) -> float:
        return round(time.time() - self.start_time, 1)

    def to_dict(self) -> dict:
        return {
            'inputTokens': self.input_tokens,
            'outputTokens': self.output_tokens,
            'cacheReadInputTokens': self.cache_read_tokens,
            'cacheWriteInputTokens': self.cache_write_tokens,
            'totalTokens': self.total_tokens,
            'estimatedCostUsd': self.estimated_cost_usd,
            'invocationCount': self.invocation_count,
            'durationSeconds': self.duration_seconds,
        }

    def flush_to_dynamodb(self):
        """Write final cost summary to DynamoDB tracking table."""
        if not TRACKING_TABLE:
            logger.warning("TRACKING_TABLE not set — skipping cost flush")
            return

        try:
            dynamodb = _get_dynamodb()
            table = dynamodb.Table(TRACKING_TABLE)
            now = datetime.now(timezone.utc).isoformat()

            table.put_item(Item={
                'pk': f"{self.user_id}#{self.slug}",
                'sk': 'cost',
                'inputTokens': self.input_tokens,
                'outputTokens': self.output_tokens,
                'cacheReadInputTokens': self.cache_read_tokens,
                'cacheWriteInputTokens': self.cache_write_tokens,
                'totalTokens': self.total_tokens,
                'estimatedCostUsd': str(self.estimated_cost_usd),  # DDB doesn't support float well
                'invocationCount': self.invocation_count,
                'durationSeconds': int(self.duration_seconds),
                'updatedAt': now,
            })
            logger.info(f"Cost flushed to DDB: slug={self.slug}, cost=${self.estimated_cost_usd}")

        except Exception as e:
            logger.error(f"Failed to flush cost to DDB: {e}")

    def flush_to_cloudwatch(self):
        """Emit cost metrics to CloudWatch for dashboarding and alarms."""
        try:
            client = _get_cw_client()
            now = datetime.now(timezone.utc)

            client.put_metric_data(
                Namespace=CW_NAMESPACE,
                MetricData=[
                    {
                        'MetricName': 'InputTokens',
                        'Dimensions': [{'Name': 'Stage', 'Value': os.environ.get('STAGE', 'dev')}],
                        'Timestamp': now,
                        'Value': self.input_tokens,
                        'Unit': 'Count',
                    },
                    {
                        'MetricName': 'OutputTokens',
                        'Dimensions': [{'Name': 'Stage', 'Value': os.environ.get('STAGE', 'dev')}],
                        'Timestamp': now,
                        'Value': self.output_tokens,
                        'Unit': 'Count',
                    },
                    {
                        'MetricName': 'EstimatedCostUsd',
                        'Dimensions': [{'Name': 'Stage', 'Value': os.environ.get('STAGE', 'dev')}],
                        'Timestamp': now,
                        'Value': self.estimated_cost_usd,
                        'Unit': 'None',
                    },
                    {
                        'MetricName': 'ResearchDuration',
                        'Dimensions': [{'Name': 'Stage', 'Value': os.environ.get('STAGE', 'dev')}],
                        'Timestamp': now,
                        'Value': self.duration_seconds,
                        'Unit': 'Seconds',
                    },
                ],
            )
            logger.info(f"Metrics emitted to CW: {CW_NAMESPACE}")

        except Exception as e:
            logger.error(f"Failed to emit CW metrics: {e}")

    def finalize(self):
        """Flush accumulated metrics to both DDB and CloudWatch."""
        self.flush_to_dynamodb()
        self.flush_to_cloudwatch()


# ─── Global tracker (set per-invocation) ──────────────────────────────
_current_tracker: CostTracker | None = None


def init_tracker(slug: str, user_id: str) -> CostTracker:
    """Initialize a new cost tracker for this research run."""
    global _current_tracker
    _current_tracker = CostTracker(slug=slug, user_id=user_id)
    return _current_tracker


def get_tracker() -> CostTracker | None:
    """Get the current active cost tracker."""
    return _current_tracker


def record_usage_callback(usage: dict):
    """
    Callback function to pass to Strands agent for automatic token tracking.

    Wire this into the agent's callback_handler or model response hook.
    """
    tracker = get_tracker()
    if tracker:
        tracker.record_usage(usage)

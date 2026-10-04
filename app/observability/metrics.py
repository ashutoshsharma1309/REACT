"""Application metrics in CloudWatch Embedded Metric Format (EMF).

Each call writes one JSON line to stdout. On ECS the awslogs driver ships stdout to CloudWatch Logs, and CloudWatch
turns EMF lines into metrics automatically. REACT makes NO CloudWatch API call and needs no cloudwatch:PutMetricData
permission. Locally (METRICS_ENABLED=false, the default) nothing is written.

Cost control: one dimension set (Environment) and a fixed list of metric names, so the number of custom metrics
stays small and constant. Never pass secrets, prompts, model output or business values as dimensions or properties.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any

# Every metric REACT emits. Anything else is rejected so a typo cannot create new (billed) custom metrics.
METRICS: dict[str, str] = {
    "AgentRunCompleted": "Count",
    "AgentRunFailed": "Count",
    "AgentRunDurationMs": "Milliseconds",
    "LLMCalls": "Count",
    "LLMInputTokens": "Count",
    "LLMOutputTokens": "Count",
    "FallbackUsed": "Count",
    "AIUnavailable": "Count",
    "LLMBudgetExhausted": "Count",
    "DecisionCreated": "Count",
    "EvidencePackStored": "Count",
    "EvidencePackFailed": "Count",
    "ToolCallFailed": "Count",
    "ActionExecuted": "Count",
    "ActionFailed": "Count",
    "DatabaseUnavailable": "Count",
    "UnhandledError": "Count",
}


def emit(values: dict[str, float], **properties: Any) -> dict[str, Any] | None:
    """Emit one EMF record with the given metric values. Returns the record (for tests) or None when disabled."""
    from app.config import settings

    if not settings.metrics_enabled or not values:
        return None
    unknown = set(values) - set(METRICS)
    if unknown:
        raise ValueError(f"unknown metric(s): {sorted(unknown)}")
    record: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{"Namespace": settings.metrics_namespace, "Dimensions": [["Environment"]], "Metrics": [{"Name": k, "Unit": METRICS[k]} for k in values]}],
        },
        "Environment": settings.app_env,
        **{k: float(v) for k, v in values.items()},
        # properties are searchable in Logs Insights but are not metrics or dimensions (no extra cost)
        **{k: (str(v) if v is not None else None) for k, v in properties.items()},
    }
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
    return record

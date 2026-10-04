"""Controlled tool execution for agents.

Every call: permission check against the agent policy -> limit accounting -> Phase 2 registry
(argument validation inside the tool) -> result summary (never the full payload) -> persisted
AgentToolCall + trace entry. The full result is returned to the agent in memory only.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.agent_runtime.limits import LimitExceeded, LimitTracker
from app.agent_runtime.trace import Tracer, _jsonable
from app.agents.policies import is_allowed
from app.agents.schemas import ToolCallRequest, ToolCallResult
from app.investigation.tools import ToolError, run_tool, tools_for
from app.models import AgentRun, AgentToolCall
from app.models.enums import ToolCallStatus

ID_KEYS = ("id", "sensor_id", "machine_id", "production_line_id", "product_id", "supplier_id", "inventory_id", "order_ids_recent", "reading_ids_recent", "delayed_batch_ids", "production_metric_ids", "metric_ids_recent", "baseline_metric_ids",
           # REACT Merchant record references
           "order_item_ids", "order_ids", "supplier_delivery_ids", "purchase_order_id", "delivery_id", "snapshot_id", "pricing_history_id", "discount_id", "competitor_signal_id", "financial_metric_ids", "expense_ids", "dependent_product_ids", "product_ids")


def summarise_result(data: Any, cap: int = 25) -> dict[str, Any]:
    """Compact, id-preserving summary of a tool result (what gets persisted)."""
    if not isinstance(data, dict):
        return {"type": type(data).__name__}
    summary: dict[str, Any] = {"count": data.get("count"), "keys": sorted(data.keys())[:30]}
    ids: dict[str, list[str]] = {}

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ID_KEYS:
                    if isinstance(v, list):
                        ids.setdefault(k, []).extend(str(x) for x in v[:cap])
                    elif v is not None:
                        ids.setdefault(k, []).append(str(v))
                elif isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:cap]:
                walk(item, depth + 1)

    walk(data)
    summary["record_ids"] = {k: sorted(set(v))[:cap] for k, v in ids.items()}
    if "summary" in data and isinstance(data["summary"], dict):
        summary["summary"] = _jsonable({k: v for k, v in list(data["summary"].items())[:20]})
    return summary


class ToolExecutor:
    def __init__(self, db: Session, run: AgentRun, tracer: Tracer, limits: LimitTracker) -> None:
        from app.domains import current_domain

        self.db, self.run, self.tracer, self.limits = db, run, tracer, limits
        self.domain = current_domain(db)
        self.sequence = len(run.tool_calls) if run.tool_calls is not None else 0
        self.results: dict[str, Any] = {}  # call_id -> full result (memory only)

    def execute(self, agent_name: str, request: ToolCallRequest) -> tuple[ToolCallResult, Any]:
        self.sequence += 1
        started = time.perf_counter()
        status, error, data, permitted = ToolCallStatus.OK, None, None, True
        if request.tool_name not in tools_for(self.domain):
            status, error, permitted = ToolCallStatus.DENIED, f"unknown tool '{request.tool_name}'", False
        elif not is_allowed(agent_name, request.tool_name, self.domain):
            status, error, permitted = ToolCallStatus.DENIED, f"{agent_name} is not permitted to call {request.tool_name}", False
        else:
            try:
                self.limits.tool_call()
                data = run_tool(request.tool_name, self.db, **request.arguments)
            except LimitExceeded as exc:
                status, error = ToolCallStatus.LIMIT_EXCEEDED, str(exc)
            except ToolError as exc:
                status, error = (ToolCallStatus.INVALID_ARGUMENTS if exc.code in ("INVALID_PARAM", "MISSING_PARAM") else ToolCallStatus.ERROR), f"{exc.code}: {exc.message}"
        duration = round((time.perf_counter() - started) * 1000, 1)
        summary = summarise_result(data) if data is not None else {}
        row = AgentToolCall(agent_run_id=self.run.id, sequence=self.sequence, agent_name=agent_name, tool_name=request.tool_name, arguments=_jsonable(request.arguments), purpose=request.purpose, status=status, permitted=permitted, duration_ms=duration, result_summary=summary, error=error)
        self.db.add(row)
        self.db.flush()
        self.run.tool_calls_count = self.sequence
        self.tracer.record(agent_name, "tool_call", f"{request.tool_name} -> {status.value}" + (f" ({summary.get('count')} records)" if summary.get("count") is not None else "") + (f": {error}" if error else ""), tool_name=request.tool_name, tool_arguments=request.arguments, tool_call_id=row.id, duration_ms=duration, status="ok" if status == ToolCallStatus.OK else "error", error=error, purpose=request.purpose)
        result = ToolCallResult(call_id=request.call_id, tool_name=request.tool_name, status=status.value, duration_ms=duration, record_count=summary.get("count"), result_summary=summary, error=error)
        if data is not None:
            self.results[request.call_id] = data
        if status != ToolCallStatus.OK:
            from app.observability.metrics import emit

            emit({"ToolCallFailed": 1}, tool=request.tool_name, status=status.value, agent=agent_name)
        if status == ToolCallStatus.LIMIT_EXCEEDED:
            raise LimitExceeded("tool_calls", self.limits.limits.max_tool_calls, self.limits.tool_calls)
        return result, data

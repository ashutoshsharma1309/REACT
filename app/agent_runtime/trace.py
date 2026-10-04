"""Persistent, structured agent trace. Stores actions, tools, ids, states and concise summaries only."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.database.base import utcnow
from app.models import AgentRun, AgentTraceEntry
from app.observability import get_logger, log_event

log = get_logger("react.agents.trace")


class Tracer:
    def __init__(self, db: Session, run: AgentRun) -> None:
        self.db = db
        self.run = run
        self.sequence = len(run.trace_entries) if run.trace_entries is not None else 0

    def record(self, agent_name: str, event_type: str, summary: str, *, state: str | None = None, tool_name: str | None = None, tool_arguments: dict[str, Any] | None = None, tool_call_id: uuid.UUID | None = None, evidence_ids: list[str] | None = None, hypothesis_ids: list[str] | None = None, duration_ms: float | None = None, status: str = "ok", error: str | None = None, **details: Any) -> AgentTraceEntry:
        self.sequence += 1
        entry = AgentTraceEntry(
            agent_run_id=self.run.id, sequence=self.sequence, timestamp=utcnow(), agent_name=agent_name, event_type=event_type, state=state or (self.run.status.value if self.run.status else None),
            tool_name=tool_name, tool_arguments=_jsonable(tool_arguments) if tool_arguments else None, tool_call_id=tool_call_id, evidence_ids=[str(e) for e in (evidence_ids or [])][:200], hypothesis_ids=[str(h) for h in (hypothesis_ids or [])],
            summary=summary[:2000], details=_jsonable(details), duration_ms=duration_ms, status=status, error=error[:2000] if error else None,
        )
        self.db.add(entry)
        self.db.flush()
        log_event(log, f"agent.{event_type}", level=40 if status == "error" else 20, agent_run_id=self.run.id, agent=agent_name, state=entry.state, tool=tool_name, summary=summary[:200], status=status, error=error, duration_ms=duration_ms)
        return entry


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)

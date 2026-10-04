"""Shared agent machinery: context, provider calls with validation / retry / fallback, tracing."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent_runtime.limits import LimitExceeded, LimitTracker
from app.agent_runtime.tool_executor import ToolExecutor
from app.agent_runtime.trace import Tracer
from app.agents.fallback import DeterministicProvider
from app.agents.prompts import localize, render_user
from app.agents.provider import LLMProvider, LLMResponse, ProviderError, StructuredOutputError
from app.investigation.tools import specs_for
from app.models import AgentRun, BusinessRisk, Incident, Machine, Opportunity, Product, ProductionLine, Supplier

T = TypeVar("T", bound=BaseModel)


class AgentFailure(RuntimeError):
    """An agent could not produce a valid output within limits (structured error for the orchestrator)."""

    def __init__(self, agent: str, reason: str) -> None:
        super().__init__(f"{agent}: {reason}")
        self.agent, self.reason = agent, reason


@dataclass
class AgentContext:
    db: Session
    run: AgentRun
    tracer: Tracer
    limits: LimitTracker
    provider: LLMProvider
    executor: ToolExecutor
    fallback_enabled: bool = True
    max_retries: int = 1

    @property
    def fallback(self) -> DeterministicProvider:
        return DeterministicProvider()


def finding_context(db: Session, subject: Incident | Opportunity | BusinessRisk) -> dict[str, Any]:
    """Structured description of a persisted finding; the only 'facts' agents start from."""
    if isinstance(subject, Incident):
        machine = db.get(Machine, subject.machine_id) if subject.machine_id else None
        line = db.get(ProductionLine, subject.production_line_id) if subject.production_line_id else (machine.production_line if machine else None)
        ctx = {"subject_kind": "incident", "subject_id": str(subject.id), "type": subject.incident_type.value, "severity": subject.severity.value, "status": subject.status.value, "title": subject.title, "description": subject.description, "detected_at": subject.detected_at.isoformat(), "affected_metrics": subject.affected_metrics, "machine_id": str(machine.id) if machine else None, "machine_name": machine.name if machine else None, "machine_code": machine.code if machine else None, "production_line_id": str(line.id) if line else None, "production_line_name": line.name if line else None}
    elif isinstance(subject, Opportunity):
        product = db.get(Product, subject.product_id) if subject.product_id else None
        line = db.get(ProductionLine, subject.production_line_id) if subject.production_line_id else None
        ctx = {"subject_kind": "opportunity", "subject_id": str(subject.id), "type": subject.opportunity_type.value, "confidence": subject.confidence.value, "status": subject.status.value, "title": subject.title, "description": subject.description, "detected_at": subject.detected_at.isoformat(), "signal": subject.signal, "affected_metrics": subject.supporting_metrics, "product_id": str(product.id) if product else None, "product_name": product.name if product else None, "category": product.category if product else None, "region": subject.region, "customer_segment": subject.customer_segment, "production_line_id": str(line.id) if line else None, "production_line_name": line.name if line else None}
    else:
        ev = subject.evidence or {}
        line = db.get(ProductionLine, uuid.UUID(ev["production_line_id"])) if ev.get("production_line_id") else None
        product = db.get(Product, uuid.UUID(ev["product_id"])) if ev.get("product_id") else None
        supplier = db.get(Supplier, subject.entity_id) if subject.entity_type == "supplier" and subject.entity_id else None
        ctx = {"subject_kind": "risk", "subject_id": str(subject.id), "type": subject.risk_type.value, "severity": subject.severity.value, "status": subject.status.value, "title": subject.title, "description": subject.description, "detected_at": subject.detected_at.isoformat(), "affected_metrics": subject.supporting_metrics, "entity_type": subject.entity_type, "entity_id": str(subject.entity_id) if subject.entity_id else None, "entity_name": subject.entity_name, "supplier_id": str(supplier.id) if supplier else None, "supplier_name": supplier.name if supplier else None, "component_type": supplier.component_type if supplier else None, "inventory_id": str(subject.entity_id) if subject.entity_type == "inventory" else None, "production_line_id": str(line.id) if line else None, "production_line_name": line.name if line else None, "product_id": str(product.id) if product else None, "product_name": product.name if product else None, "category": subject.entity_name if subject.entity_type == "category" else (product.category if product else None)}
    ev = subject.evidence or {}
    if ev.get("domain") == "merchant":  # REACT Merchant: store, stock item, supplier, category and zone come from the finding
        ctx["domain"] = "merchant"
        for key in ("inventory_id", "store_id", "store_name", "category", "region"):
            if ev.get(key) and not ctx.get(key):
                ctx[key] = ev[key]
        if ev.get("supplier_id") and not ctx.get("supplier_id"):
            sup = db.get(Supplier, uuid.UUID(ev["supplier_id"]))
            if sup:
                ctx["supplier_id"], ctx["supplier_name"] = str(sup.id), sup.name
        ctx.pop("production_line_id", None)
        ctx.pop("production_line_name", None)
        ctx.pop("component_type", None)
    else:
        ctx["domain"] = "manufacturing"
    records = (subject.evidence or {}).get("records", {})
    ctx["detection_record_ids"] = {k: (v[:10] if isinstance(v, list) else v) for k, v in records.items()} if isinstance(records, dict) else {}
    return ctx


def tool_catalogue(names: frozenset[str], domain: str = "manufacturing") -> list[dict[str, Any]]:
    """Tool descriptions for the model, in a provider-neutral shape (name, description, input_schema)."""
    specs = specs_for(domain)
    out = []
    for name in sorted(names):
        spec = specs.get(name)
        if not spec:
            continue
        out.append({"name": name, "description": spec["description"], "input_schema": {"type": "object", "properties": {p: {"type": "string", "description": d} for p, d in spec["params"].items()}}})
    return out


class BaseAgent:
    name: str = "agent"
    system_prompt: str = ""

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    # ---- provider calls with accounting, validation, retry and fallback ----------------------------
    def _account(self, resp: LLMResponse) -> None:
        run = self.ctx.run
        run.llm_calls_count = self.ctx.limits.llm_calls
        run.input_tokens += resp.input_tokens or 0
        run.output_tokens += resp.output_tokens or 0
        if resp.provider not in ("deterministic", ""):
            run.llm_used = True

    def structured(self, task: str, system: str, context: dict[str, Any], schema: type[T], validator: Callable[[T], None] | None = None) -> T:
        context = {**context, "task": task}
        attempts = 1 + max(0, self.ctx.max_retries)
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                self.ctx.limits.llm_call()
                ctx_in = {**context, "previous_error": last_error} if last_error else context
                obj, resp = self.ctx.provider.generate_structured(localize(system, self.ctx.executor.domain), [{"role": "user", "content": render_user(task, ctx_in)}], schema, context=ctx_in)
                self._account(resp)
                if validator:
                    validator(obj)
                self.ctx.tracer.record(self.name, "llm_call", f"{task}: valid {schema.__name__} from {resp.provider}", duration_ms=round((time.perf_counter() - started) * 1000, 1), provider=resp.provider, model=resp.model, attempt=attempt, input_tokens=resp.input_tokens, output_tokens=resp.output_tokens)
                return obj
            except (StructuredOutputError, ValueError) as exc:
                last_error = str(exc)
                self.ctx.tracer.record(self.name, "validation", f"{task}: output rejected ({last_error[:160]})", status="error", error=last_error, attempt=attempt, duration_ms=round((time.perf_counter() - started) * 1000, 1))
            except LimitExceeded as exc:
                self.ctx.tracer.record(self.name, "limit", f"{task}: {exc}", status="error", error=str(exc))
                raise  # limits are never absorbed by a fallback: the orchestrator ends the run as INSUFFICIENT_EVIDENCE
            except ProviderError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self.ctx.tracer.record(self.name, "retry" if attempt < attempts else "error", f"{task}: provider failure ({last_error[:160]})", status="error", error=last_error, attempt=attempt, duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return self._fallback_structured(task, context, schema, validator, last_error)

    def _fallback_structured(self, task: str, context: dict[str, Any], schema: type[T], validator: Callable[[T], None] | None, last_error: str | None) -> T:
        if not self.ctx.fallback_enabled or not self.ctx.provider.is_llm:
            raise AgentFailure(self.name, f"could not obtain a valid {schema.__name__} ({last_error})")
        obj, resp = self.ctx.fallback.generate_structured("", [], schema, context=context)
        if validator:
            validator(obj)
        self.ctx.run.fallback_used = True
        self.ctx.tracer.record(self.name, "fallback", f"{task}: deterministic provider used after LLM failure ({(last_error or '')[:120]})", status="warning", provider="deterministic")
        return obj

    def tool_turn(self, task: str, system: str, context: dict[str, Any], tools: list[dict[str, Any]]) -> LLMResponse:
        context = {**context, "task": task}
        attempts = 1 + max(0, self.ctx.max_retries)
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                self.ctx.limits.llm_call()
                resp = self.ctx.provider.generate_with_tools(localize(system, self.ctx.executor.domain), [{"role": "user", "content": render_user(task, context)}], tools, context=context)
                self._account(resp)
                self.ctx.tracer.record(self.name, "llm_call", f"{task}: {len(resp.tool_calls)} tool call(s) requested" + (f" ({', '.join(c.tool_name for c in resp.tool_calls)})" if resp.tool_calls else ""), duration_ms=round((time.perf_counter() - started) * 1000, 1), provider=resp.provider, model=resp.model, attempt=attempt)
                return resp
            except LimitExceeded as exc:
                self.ctx.tracer.record(self.name, "limit", f"{task}: {exc}", status="error", error=str(exc))
                raise
            except ProviderError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self.ctx.tracer.record(self.name, "retry" if attempt < attempts else "error", f"{task}: provider failure ({last_error[:160]})", status="error", error=last_error, attempt=attempt)
        if not self.ctx.fallback_enabled or not self.ctx.provider.is_llm:
            raise AgentFailure(self.name, f"tool selection failed ({last_error})")
        resp = self.ctx.fallback.generate_with_tools("", [], tools, context=context)
        self.ctx.run.fallback_used = True
        self.ctx.tracer.record(self.name, "fallback", f"{task}: deterministic tool plan used after LLM failure", status="warning", provider="deterministic")
        return resp

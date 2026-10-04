"""LLM provider abstraction.

    LLMProvider
    ├── generate()              free text (used sparingly)
    ├── generate_structured()   JSON validated against a Pydantic schema
    └── generate_with_tools()   tool-use turn: returns ToolCallRequests (or none = done)

Implementations:
    DeterministicProvider  rule-based, no network, always available (app/agents/fallback.py)
    MockLLMProvider        scripted responses for tests
    BedrockProvider        Amazon Bedrock `converse` API through boto3 (credentials from the standard chain)
    UnavailableProvider    always fails with ProviderUnavailable: AI-outage drill, exhausted LLM budget, misconfiguration

Providers never see database credentials and never receive secrets in prompts.
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.schemas import ToolCallRequest
from app.observability import get_logger, log_event

log = get_logger("react.agents.provider")
T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    """Base class for provider failures (safe message, no secrets)."""


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class StructuredOutputError(ProviderError):
    """The model answered, but the answer did not validate against the schema."""


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    stop_reason: str | None = None
    raw_json: dict[str, Any] | None = None


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""
    is_llm: bool = True  # False for the deterministic rule-based provider

    @abstractmethod
    def generate(self, system: str, messages: list[dict[str, str]], *, max_tokens: int | None = None, context: dict[str, Any] | None = None) -> LLMResponse: ...

    @abstractmethod
    def generate_structured(self, system: str, messages: list[dict[str, str]], schema: type[T], *, context: dict[str, Any] | None = None) -> tuple[T, LLMResponse]: ...

    @abstractmethod
    def generate_with_tools(self, system: str, messages: list[dict[str, str]], tools: list[dict[str, Any]], *, context: dict[str, Any] | None = None) -> LLMResponse: ...

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "is_llm": self.is_llm}


# ---- helpers -----------------------------------------------------------------------------------------
def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates code fences and prose)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise StructuredOutputError("no JSON object found in model output")
        candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid JSON in model output: {exc.msg}") from None
    if not isinstance(obj, dict):
        raise StructuredOutputError("model output is not a JSON object")
    return obj


def validate_into(schema: type[T], payload: dict[str, Any]) -> T:
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])
        raise StructuredOutputError(f"output does not match {schema.__name__}: {errors}") from None


def schema_instruction(schema: type[BaseModel]) -> str:
    return "Respond with ONLY a single JSON object (no prose, no markdown) that validates against this JSON schema:\n" + json.dumps(schema.model_json_schema(), indent=None)


# ---- Mock provider (tests) -----------------------------------------------------------------------------
class MockLLMProvider(LLMProvider):
    """Scripted provider. Each queued item is one of:
    * dict                       -> structured output payload (validated) / or {"tool_calls": [...]} for a tool turn
    * LLMResponse                -> returned as is
    * Exception instance         -> raised
    * callable(context) -> any of the above
    When the queue is empty, `default` (a callable(context, schema) or None) is used; None raises ProviderUnavailable.
    """

    name = "mock"
    is_llm = True

    def __init__(self, responses: list[Any] | None = None, model: str = "mock-model", default: Callable[..., Any] | None = None) -> None:
        self.model = model
        self.queue: list[Any] = list(responses or [])
        self.default = default
        self.calls: list[dict[str, Any]] = []

    def _next(self, kind: str, context: dict[str, Any] | None, schema: type[BaseModel] | None = None) -> Any:
        self.calls.append({"kind": kind, "task": (context or {}).get("task"), "schema": schema.__name__ if schema else None})
        if self.queue:
            item = self.queue.pop(0)
        elif self.default is not None:
            item = self.default(context, schema)
        else:
            raise ProviderUnavailable("mock provider has no scripted response left")
        if callable(item) and not isinstance(item, Exception):
            item = item(context)
        if isinstance(item, Exception):
            raise item
        return item

    def generate(self, system, messages, *, max_tokens=None, context=None) -> LLMResponse:
        item = self._next("generate", context)
        return item if isinstance(item, LLMResponse) else LLMResponse(text=str(item), provider=self.name, model=self.model)

    def generate_structured(self, system, messages, schema, *, context=None):
        item = self._next("structured", context, schema)
        if isinstance(item, LLMResponse):
            payload = item.raw_json if item.raw_json is not None else extract_json(item.text)
            return validate_into(schema, payload), item
        if isinstance(item, BaseModel):
            return item, LLMResponse(provider=self.name, model=self.model, raw_json=item.model_dump())
        payload = item if isinstance(item, dict) else extract_json(str(item))
        return validate_into(schema, payload), LLMResponse(provider=self.name, model=self.model, raw_json=payload, input_tokens=100, output_tokens=50)

    def generate_with_tools(self, system, messages, tools, *, context=None) -> LLMResponse:
        item = self._next("tools", context)
        if isinstance(item, LLMResponse):
            return item
        calls = [ToolCallRequest.model_validate(c) if isinstance(c, dict) else c for c in (item.get("tool_calls", []) if isinstance(item, dict) else [])]
        return LLMResponse(text=item.get("text", "") if isinstance(item, dict) else "", tool_calls=calls, provider=self.name, model=self.model, input_tokens=100, output_tokens=30, stop_reason="tool_use" if calls else "end_turn")


# ---- Amazon Bedrock ---------------------------------------------------------------------------------------
class BedrockProvider(LLMProvider):
    """Amazon Bedrock `converse` API. boto3 is imported lazily; credentials come from the standard AWS chain."""

    name = "bedrock"
    is_llm = True

    def __init__(self, model: str, region: str, timeout_seconds: float = 30.0, max_tokens: int = 2048, temperature: float = 0.0, client: Any = None) -> None:
        self.model, self.region, self.timeout, self.max_tokens, self.temperature = model, region, timeout_seconds, max_tokens, temperature
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover
                raise ProviderUnavailable("boto3 is not installed") from exc
            self._client = boto3.client("bedrock-runtime", region_name=self.region, config=Config(read_timeout=self.timeout, connect_timeout=min(10.0, self.timeout), retries={"max_attempts": 0}))
        return self._client

    def _converse(self, system: str, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None, max_tokens: int | None = None) -> LLMResponse:
        started = time.perf_counter()
        body: dict[str, Any] = {
            "modelId": self.model,
            "system": [{"text": system}],
            "messages": [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
            "inferenceConfig": {"maxTokens": max_tokens or self.max_tokens, "temperature": self.temperature},
        }
        if tools:
            body["toolConfig"] = {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"], "inputSchema": {"json": t["input_schema"]}}} for t in tools]}
        try:
            resp = self.client.converse(**body)
        except Exception as exc:  # noqa: BLE001  (botocore exception classes vary; keep the message generic)
            name = type(exc).__name__
            if "Timeout" in name or "timed out" in str(exc).lower():
                raise ProviderTimeout(f"Bedrock request timed out ({name})") from None
            raise ProviderError(f"Bedrock request failed ({name})") from None
        return self.parse_converse_response(resp, time.perf_counter() - started)

    def parse_converse_response(self, resp: dict[str, Any], elapsed_s: float = 0.0) -> LLMResponse:
        content = (resp.get("output", {}).get("message", {}) or {}).get("content", []) or []
        text_parts, calls = [], []
        for block in content:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                calls.append(ToolCallRequest(call_id=str(tu.get("toolUseId") or f"tc-{len(calls)}"), tool_name=tu.get("name", ""), arguments=dict(tu.get("input") or {})))
        usage = resp.get("usage", {}) or {}
        return LLMResponse(text="\n".join(text_parts), tool_calls=calls, provider=self.name, model=self.model, input_tokens=int(usage.get("inputTokens", 0) or 0), output_tokens=int(usage.get("outputTokens", 0) or 0), latency_ms=round(elapsed_s * 1000, 1), stop_reason=resp.get("stopReason"))

    def generate(self, system, messages, *, max_tokens=None, context=None) -> LLMResponse:
        return self._converse(system, messages, max_tokens=max_tokens)

    def generate_structured(self, system, messages, schema, *, context=None):
        msgs = list(messages)
        msgs[-1] = {"role": msgs[-1]["role"], "content": msgs[-1]["content"] + "\n\n" + schema_instruction(schema)}
        resp = self._converse(system, msgs)
        payload = extract_json(resp.text)
        resp.raw_json = payload
        return validate_into(schema, payload), resp

    def generate_with_tools(self, system, messages, tools, *, context=None) -> LLMResponse:
        return self._converse(system, messages, tools=tools)


# ---- Unavailable (outage drill / budget exhausted / misconfigured) ------------------------------------------
class UnavailableProvider(LLMProvider):
    """Stands in for an LLM that cannot be reached. It never makes a network call.

    It reports is_llm=True so the normal failure path applies: the agent records the failure in the trace and either
    uses the deterministic fallback (AGENT_FALLBACK_TO_DETERMINISTIC=true) or the run fails with "AI investigation temporarily unavailable".
    """

    name = "unavailable"
    is_llm = True

    def __init__(self, reason: str, model: str = "") -> None:
        self.reason = reason
        self.model = model or "none"

    def _fail(self):
        raise ProviderUnavailable(self.reason)

    def generate(self, system, messages, *, max_tokens=None, context=None) -> LLMResponse:
        self._fail()

    def generate_structured(self, system, messages, schema, *, context=None):
        self._fail()

    def generate_with_tools(self, system, messages, tools, *, context=None) -> LLMResponse:
        self._fail()

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "reason": self.reason}


# ---- factory ---------------------------------------------------------------------------------------------
def build_provider(name: str | None = None) -> LLMProvider:
    """Build the configured provider. Never embeds credentials; Bedrock uses the boto3 credential chain."""
    from app.agents.fallback import DeterministicProvider
    from app.config import settings

    name = (name or settings.llm_provider).lower()
    if name == "deterministic":
        return DeterministicProvider()
    if name == "bedrock":
        if not settings.llm_model:
            log_event(log, "provider.misconfigured", level=30, provider=name, reason="LLM_MODEL is not set")
            return UnavailableProvider("Bedrock is selected but LLM_MODEL is not configured")
        return BedrockProvider(model=settings.llm_model, region=settings.aws_region, timeout_seconds=settings.llm_timeout_seconds, max_tokens=settings.llm_max_tokens, temperature=settings.llm_temperature)
    if name == "mock":
        return MockLLMProvider()
    if name == "unavailable":
        return UnavailableProvider("LLM provider set to 'unavailable' (AI failure drill)")
    log_event(log, "provider.unknown", level=30, provider=name)
    raise ProviderUnavailable(f"unknown LLM provider '{name}'")

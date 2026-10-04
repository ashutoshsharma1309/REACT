"""Phase 6: environment configuration, provider selection and logging safety. No AWS credentials or network needed."""
from __future__ import annotations

import io
import json
import logging

from app.agents.provider import BedrockProvider, ProviderUnavailable, UnavailableProvider, build_provider
from app.config import settings
from app.config.settings import Settings
from app.observability.logging import REDACTED, JsonFormatter, log_event, redact

import pytest


def test_database_url_is_composed_from_ecs_style_variables(monkeypatch):
    for k in ("DATABASE_URL", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_PORT", "DB_SSLMODE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_HOST", "react-db.example.internal")
    monkeypatch.setenv("DB_USER", "react")
    monkeypatch.setenv("DB_PASSWORD", "p@ss:w/rd%")  # generated secrets contain URL-special characters
    monkeypatch.setenv("DB_NAME", "react")
    monkeypatch.setenv("DB_SSLMODE", "require")
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://react:p%40ss%3Aw%2Frd%25@react-db.example.internal:5432/react?sslmode=require"
    from sqlalchemy.engine import make_url

    assert make_url(s.database_url).password == "p@ss:w/rd%"  # round-trips exactly


def test_local_defaults_need_no_aws(monkeypatch):
    for k in ("DB_HOST", "LLM_PROVIDER", "LLM_MODEL", "STORAGE_BACKEND", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.db_host is None and s.database_url.startswith("postgresql+psycopg://")
    assert s.llm_provider == "deterministic" and s.llm_model == ""  # no model ID hard-coded
    assert s.storage_backend == "local" and s.metrics_enabled is False and s.is_local


def test_bedrock_without_model_fails_safe(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "")
    p = build_provider("bedrock")
    assert isinstance(p, UnavailableProvider) and p.is_llm and "LLM_MODEL" in p.reason
    with pytest.raises(ProviderUnavailable):
        p.generate("s", [{"role": "user", "content": "u"}])


def test_bedrock_model_and_region_come_from_configuration(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "configured-model-id")
    monkeypatch.setattr(settings, "aws_region", "ap-south-1")
    p = build_provider("bedrock")
    assert isinstance(p, BedrockProvider) and p.model == "configured-model-id" and p.region == "ap-south-1"
    assert p._client is None  # nothing is created (and no credential is looked up) until the first call


def test_unavailable_provider_for_failure_drill():
    p = build_provider("unavailable")
    assert isinstance(p, UnavailableProvider) and p.describe()["reason"]


def test_log_redaction_masks_secrets():
    assert redact({"password": "x", "DB_PASSWORD": "y", "api_key": "z", "input_tokens": 12}) == {"password": REDACTED, "DB_PASSWORD": REDACTED, "api_key": REDACTED, "input_tokens": 12}
    assert "hunter2" not in redact("postgresql+psycopg://react:hunter2@db:5432/react")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("react.test.redaction")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    try:
        log_event(logger, "test.event", secret="s3cr3t", url="postgresql://u:pw123@h/db", tokens=5)
    finally:
        logging.disable(logging.WARNING)
        logger.removeHandler(handler)
    line = json.loads(stream.getvalue())
    assert line["secret"] == REDACTED and "pw123" not in line["url"] and line["tokens"] == 5

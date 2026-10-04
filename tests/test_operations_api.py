"""Phase 6 API: readiness, /api prefix, AI-failure handling, LLM budget, evidence packs and metrics."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from app.agent_runtime.guard import guard_provider
from app.agents.provider import MockLLMProvider, UnavailableProvider
from app.config import settings
from app.database.base import utcnow
from app.models import AgentRun, Artifact
from app.models.enums import AgentRunStatus, InvestigationSubject
from app.observability import metrics
from app.storage import StorageError


def _bearing_incident(client) -> str:
    client.post("/simulation/bearing-failure")
    det = client.post("/detection/run").json()
    return next(i for i in det["incidents"] if i["type"] == "MACHINE_DEGRADATION")["incident_id"]


# ---- health / readiness -------------------------------------------------------------------------------------
def test_health_is_liveness_and_ready_reports_checks_without_secrets(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_create_schema", True)  # local mode: schema created without Alembic
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["version"] == "0.6.0"
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready" and set(body["checks"]) >= {"database", "migrations", "data", "storage", "ai"}
    assert body["checks"]["ai"]["reachability"].startswith("not probed")
    text = r.text.lower()
    for leak in ("postgresql", "password", "secret", settings.database_url.lower()):
        assert leak not in text


def test_ready_fails_when_migrations_are_not_applied(client, monkeypatch):
    monkeypatch.setattr(settings, "auto_create_schema", False)  # AWS mode: Alembic must be at head
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["migrations"]["status"] == "BEHIND"


def test_ready_fails_when_s3_bucket_missing(client, monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "artifacts_bucket", None)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["storage"]["status"] == "MISCONFIGURED"


def test_api_prefix_from_cloudfront_is_stripped(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/overview").status_code == 200
    assert client.get("/apiary").status_code == 404  # only the exact prefix segment is stripped


# ---- AI failure handling --------------------------------------------------------------------------------------
def test_ai_outage_without_fallback_returns_503_and_invents_nothing(client, db):
    inc_id = _bearing_incident(client)
    assert client.post("/demo/ai-outage", json={"mode": "fail"}).json()["mode"] == "fail"
    r = client.post("/react/run", json={"incident_id": inc_id})
    assert r.status_code == 503 and r.headers["retry-after"] == "60"
    detail = r.json()["detail"]
    assert detail.startswith("AI investigation temporarily unavailable") and "no conclusion or recommendation" in detail
    run = db.query(AgentRun).filter(AgentRun.incident_id == inc_id).order_by(AgentRun.created_at.desc()).first()
    assert run.status == AgentRunStatus.FAILED and run.provider == "unavailable" and run.result["hypothesis_type"] is None
    assert run.result["evidence_support_score"] is None and client.get(f"/incidents/{inc_id}/decision").status_code == 404
    events = [t["event_type"] for t in client.get(f"/agent-runs/{run.id}/trace").json()]
    assert "provider_unavailable" in events and "error" in events
    assert client.get("/ready").json()["checks"]["ai"]["status"] == "OUTAGE_DRILL"


def test_ai_outage_with_fallback_is_labelled(client):
    inc_id = _bearing_incident(client)
    client.post("/demo/ai-outage", json={"mode": "fallback"})
    d = client.post("/react/run", json={"incident_id": inc_id}).json()
    assert d["status"] == "AWAITING_APPROVAL" and d["fallback_used"] is True and d["llm_used"] is False
    assert d["hypothesis_type"] == "BEARING_DEGRADATION"  # same deterministic answer, clearly marked as fallback


def test_outage_drill_survives_demo_reset_and_can_be_disabled(client, monkeypatch):
    client.post("/demo/ai-outage", json={"mode": "fail"})
    client.post("/simulation/reset")
    assert client.get("/demo/ai-outage").json()["mode"] == "fail"
    client.post("/demo/ai-outage", json={"mode": "off"})
    assert client.post("/demo/ai-outage", json={"mode": "broken"}).status_code == 422
    monkeypatch.setattr(settings, "demo_controls_enabled", False)
    assert client.post("/demo/ai-outage", json={"mode": "fail"}).status_code == 403


def test_hourly_llm_budget_replaces_provider(db, monkeypatch):
    monkeypatch.setattr(settings, "llm_hourly_call_budget", 5)
    mock = MockLLMProvider()
    assert guard_provider(db, mock, True).provider is mock  # under budget
    db.add(AgentRun(subject_type=InvestigationSubject.INCIDENT, status=AgentRunStatus.COMPLETED, provider="bedrock", model="m", llm_calls_count=5, created_at=utcnow()))
    db.add(AgentRun(subject_type=InvestigationSubject.INCIDENT, status=AgentRunStatus.COMPLETED, provider="bedrock", model="m", llm_calls_count=50, created_at=utcnow() - timedelta(hours=2)))  # outside window
    db.add(AgentRun(subject_type=InvestigationSubject.INCIDENT, status=AgentRunStatus.COMPLETED, provider="deterministic", model="", llm_calls_count=50, created_at=utcnow()))  # not an LLM
    db.flush()
    g = guard_provider(db, mock, True)
    assert isinstance(g.provider, UnavailableProvider) and "budget" in g.reason and g.fallback_enabled


# ---- evidence packs ------------------------------------------------------------------------------------------
def test_approval_writes_verifiable_evidence_pack(client):
    inc_id = _bearing_incident(client)
    did = client.post("/react/run", json={"incident_id": inc_id}).json()["decision_id"]
    assert client.get(f"/decisions/{did}/artifacts").json() == []
    client.post(f"/decisions/{did}/approve", json={"reviewed_by": "plant.manager"})
    arts = client.get(f"/decisions/{did}/artifacts").json()
    assert len(arts) == 1 and arts[0]["status"] == "STORED" and arts[0]["trigger"] == "decision.executed" and arts[0]["storage_backend"] == "local"
    dl = client.get(f"/api{arts[0]['download_path']}")
    assert dl.status_code == 200 and hashlib.sha256(dl.content).hexdigest() == arts[0]["sha256"] == dl.headers["x-content-sha256"]
    pack = json.loads(dl.content)
    assert pack["provenance"]["data"] == "SYNTHETIC" and pack["decision"]["id"] == did and pack["approvals"][0]["reviewed_by"] == "plant.manager"
    assert pack["actions"][0]["simulated"] is True and pack["evidence"] and pack["agent_trace"]
    assert pack["decision"]["expected_impact"]["estimated_total"] == pack["investigation"]["impact"]["estimated_total"]  # copied, not recomputed
    raw = dl.text.lower()
    assert "chain_of_thought" not in raw and "reasoning_content" not in raw and "thinking" not in raw
    trace = client.get(f"/decisions/{did}/trace").json()["agent_trace"]
    assert any(t["event_type"] == "artifact" and "Evidence pack stored" in t["summary"] for t in trace)


def test_storage_failure_never_blocks_approval(client, monkeypatch):
    inc_id = _bearing_incident(client)
    did = client.post("/react/run", json={"incident_id": inc_id}).json()["decision_id"]

    class Broken:
        backend = "s3"

        def put(self, *a, **k):
            raise StorageError("S3 upload failed (AccessDenied)")

    monkeypatch.setattr("app.services.artifact_service.build_store", lambda *a, **k: Broken())
    ok = client.post(f"/decisions/{did}/approve", json={"reviewed_by": "plant.manager"})
    assert ok.status_code == 200 and ok.json()["status"] == "EXECUTED"
    art = client.get(f"/decisions/{did}/artifacts").json()[0]
    assert art["status"] == "FAILED" and art["error"] == "S3 upload failed (AccessDenied)" and art["download_path"] is None
    assert client.get(f"/artifacts/{art['id']}/download").status_code == 404


def test_rejection_writes_evidence_pack_and_tampering_is_detected(client, db):
    inc_id = _bearing_incident(client)
    did = client.post("/react/run", json={"incident_id": inc_id}).json()["decision_id"]
    client.post(f"/decisions/{did}/reject", json={"reviewed_by": "plant.manager", "reason": "already scheduled"})
    art = client.get(f"/decisions/{did}/artifacts").json()[0]
    assert art["trigger"] == "decision.rejected" and art["status"] == "STORED"
    row = db.get(Artifact, art["id"])
    from pathlib import Path

    Path(settings.storage_local_dir, row.location).write_bytes(b'{"tampered": true}')
    r = client.get(f"/artifacts/{art['id']}/download")
    assert r.status_code == 502 and "integrity" in r.json()["detail"]


# ---- metrics -------------------------------------------------------------------------------------------------
def test_emf_metrics_only_when_enabled(capsys, monkeypatch):
    assert metrics.emit({"DecisionCreated": 1}) is None
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    rec = metrics.emit({"AgentRunCompleted": 1, "LLMCalls": 3}, agent_run_id="r1")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line == rec and line["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "REACT" and line["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Environment"]]
    assert line["Environment"] == "staging" and line["LLMCalls"] == 3.0
    import pytest

    with pytest.raises(ValueError):
        metrics.emit({"TypoMetric": 1})


# ---- infrastructure failures fail safely ------------------------------------------------------------------------
def test_database_unavailable_returns_503_without_internals(client):
    from sqlalchemy.exc import OperationalError

    from app.api.deps import get_db
    from app.main import app

    class DeadSession:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise OperationalError("SELECT 1", {}, Exception("connection to server at \"db.internal\" (10.0.0.5), port 5432 failed: password authentication failed"))
            return fail

    app.dependency_overrides[get_db] = lambda: DeadSession()
    r = client.get("/overview")
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["detail"]
    assert "10.0.0.5" not in r.text and "password" not in r.text and r.headers["retry-after"] == "30"
    h = client.get("/health")  # liveness never fails on the database; it reports it
    assert h.status_code == 200 and h.json()["database"] == "DOWN" and h.json()["status"] == "degraded"
    assert client.get("/ready").status_code == 503


def test_unhandled_error_is_generic_and_counted(client, monkeypatch, capsys):
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr("app.services.product_service.overview", lambda db: (_ for _ in ()).throw(RuntimeError("secret internals at /srv/app")))
    r = client.get("/overview")
    assert r.status_code == 500 and r.json()["detail"].startswith("Internal error. Reference ") and "internals" not in r.text
    assert any('"UnhandledError": 1.0' in line for line in capsys.readouterr().out.splitlines())

"""Capture real API responses as frontend test fixtures (frontend/src/test/fixtures/*.json).

Runs the deterministic REACT Merchant demos (baseline, stockout crisis, demand opportunity) against a running
backend whose dataset is the merchant domain, so the component tests exercise the actual API contract instead of
hand-written JSON. The long Products / Inventory lists are trimmed to their first rows plus every row with a
finding; nothing else is edited.

Usage:  python scripts/capture_ui_fixtures.py [http://127.0.0.1:8000]
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src" / "test" / "fixtures"
LIST_ROWS = 25


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def call_status(method: str, path: str, body: dict | None = None):
    """Like call(), but returns (status, body) for error responses too (503 from /ready or an AI outage)."""
    try:
        return 200, call(method, path, body)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def trimmed(rows: list[dict]) -> list[dict]:
    return [r for i, r in enumerate(rows) if i < LIST_ROWS or r.get("findings")]


def save(name: str, data) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, default=str))
    print("saved", name)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.json"):  # fixtures from an earlier domain must not linger
        stale.unlink()
    call("POST", "/demo/run", {"scenario": "baseline"})
    save("overview_healthy", call("GET", "/overview"))
    save("merchant", call("GET", "/merchant"))
    save("merchant_templates", call("GET", "/merchant/templates"))
    save("demo_scenarios", call("GET", "/demo/scenarios"))

    demo = call("POST", "/demo/run", {"scenario": "stockout_crisis"})
    fid = demo["featured"]["id"]
    assert demo["featured"]["kind"] == "risk", demo["featured"]
    save("overview_critical", call("GET", "/overview"))
    save("findings_risks", call("GET", "/findings?kind=risk_all&include_closed=false"))
    save("finding_detected", call("GET", f"/findings/risk/{fid}"))
    save("products", trimmed(call("GET", "/products")))
    save("inventory", trimmed(call("GET", "/inventory")))
    save("suppliers", call("GET", "/suppliers"))
    decision = call("POST", "/react/run", {"risk_id": fid})
    save("decision_awaiting", decision)
    save("finding_awaiting", call("GET", f"/findings/risk/{fid}"))
    inv_id = decision["investigation_id"]
    save("investigation", call("GET", f"/investigations/{inv_id}"))
    save("evidence", call("GET", f"/investigations/{inv_id}/evidence"))
    save("graph", call("GET", f"/investigations/{inv_id}/graph"))
    save("agent_run", call("GET", f"/agent-runs/{decision['agent_run_id']}"))
    save("decision_executed", call("POST", f"/decisions/{decision['decision_id']}/approve", {"reviewed_by": "Store owner", "execute": True}))
    save("artifacts_executed", call("GET", f"/decisions/{decision['decision_id']}/artifacts"))
    save("overview_after", call("GET", "/overview"))
    save("activity", call("GET", "/activity?limit=100"))
    save("decisions", call("GET", "/decisions?limit=200"))

    opp = call("POST", "/demo/run", {"scenario": "demand_opportunity"})
    assert opp["featured"]["kind"] == "opportunity", opp["featured"]
    save("findings_opportunities", call("GET", "/findings?kind=opportunity&include_closed=false"))
    save("finding_opportunity", call("GET", f"/findings/opportunity/{opp['featured']['id']}"))

    # readiness and the AI-failure drill (no AWS involved: the drill only makes the provider unavailable)
    save("ready", call_status("GET", "/ready")[1])
    call("POST", "/demo/ai-outage", {"mode": "fail"})
    status, body = call_status("POST", "/react/run", {"opportunity_id": opp["featured"]["id"]})
    assert status == 503, status
    save("ai_unavailable", body)
    call("POST", "/demo/ai-outage", {"mode": "fallback"})
    save("decision_fallback", call("POST", "/react/run", {"opportunity_id": opp["featured"]["id"]}))
    call("POST", "/demo/ai-outage", {"mode": "off"})


if __name__ == "__main__":
    main()

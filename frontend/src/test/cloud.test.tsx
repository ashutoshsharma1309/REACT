// Phase 6 UI behaviour, using responses captured from the real backend (scripts/capture_ui_fixtures.py):
// REACT Merchant: AI unavailable (503), labelled deterministic fallback, evidence-pack audit record, readiness and the AI-failure drill.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderApp } from "./utils";
import type { Artifact, Decision, Finding, Readiness } from "../lib/types";

import agentRun from "./fixtures/agent_run.json";
import aiUnavailable from "./fixtures/ai_unavailable.json";
import artifactsExecuted from "./fixtures/artifacts_executed.json";
import decisionAwaiting from "./fixtures/decision_awaiting.json";
import decisionExecuted from "./fixtures/decision_executed.json";
import decisionFallback from "./fixtures/decision_fallback.json";
import evidence from "./fixtures/evidence.json";
import findingAwaiting from "./fixtures/finding_awaiting.json";
import findingDetected from "./fixtures/finding_detected.json";
import graph from "./fixtures/graph.json";
import investigation from "./fixtures/investigation.json";
import overviewCritical from "./fixtures/overview_critical.json";
import ready from "./fixtures/ready.json";

const risk = findingDetected as unknown as Finding;
const dec = decisionAwaiting as unknown as Decision;
const arts = artifactsExecuted as unknown as Artifact[];
const rdy = ready as unknown as Readiness;

afterEach(() => vi.unstubAllGlobals());

function caseRoutes(finding: unknown, decision: unknown) {
  const d = decision as Decision;
  return {
    "GET /overview": overviewCritical,
    [`GET /findings/risk/${risk.id}`]: finding,
    [`GET /decisions/${d.decision_id}`]: decision,
    [`GET /investigations/${d.investigation_id}`]: investigation,
    [`GET /investigations/${d.investigation_id}/evidence`]: evidence,
    [`GET /investigations/${d.investigation_id}/graph`]: graph,
    [`GET /agent-runs/${d.agent_run_id}`]: agentRun,
  };
}

describe("AI failure handling", () => {
  it("shows 'AI investigation temporarily unavailable' on a 503 and never shows a conclusion", async () => {
    mockApi({ ...caseRoutes(findingDetected, decisionAwaiting), "POST /react/run": aiUnavailable }, { status: { "POST /react/run": 503 } });
    renderApp(`/cases/risk/${risk.id}`);
    await userEvent.click(await screen.findByRole("button", { name: /investigate with react/i }));
    const panel = await screen.findByRole("alert");
    expect(within(panel).getByRole("heading", { name: /AI investigation temporarily unavailable/i })).toBeInTheDocument();
    expect(within(panel).getByText(/did not produce a conclusion or a recommendation, and nothing in your business was changed/i)).toBeInTheDocument();
    expect(within(panel).getByText((aiUnavailable as { detail: string }).detail)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /what should i do/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/likely cause/i)).not.toBeInTheDocument();
  });

  it("labels a recommendation produced by the rule-based fallback", async () => {
    const fb = decisionFallback as unknown as Decision;
    expect(fb.fallback_used).toBe(true);
    mockApi(caseRoutes(findingAwaiting, { ...decisionAwaiting, fallback_used: true, llm_used: false }));
    renderApp(`/cases/risk/${risk.id}`);
    expect(await screen.findByText(/AI reasoning was unavailable for part of this investigation/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing was guessed/i)).toBeInTheDocument();
  });

  it("shows no fallback label when the fallback was not used", async () => {
    mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}`);
    await screen.findByRole("heading", { name: /what should i do/i });
    expect(screen.queryByText(/AI reasoning was unavailable/i)).not.toBeInTheDocument();
  });
});

describe("Evidence pack (audit record)", () => {
  it("links the stored evidence pack with its SHA-256 after approval", async () => {
    const exec = decisionExecuted as unknown as Decision;
    mockApi({ ...caseRoutes(findingAwaiting, decisionAwaiting), [`POST /decisions/${dec.decision_id}/approve`]: decisionExecuted, [`GET /decisions/${exec.decision_id}/artifacts`]: artifactsExecuted });
    renderApp(`/cases/risk/${risk.id}`);
    await userEvent.type(await screen.findByLabelText(/your name/i), "Test reviewer");
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    const link = await screen.findByRole("link", { name: /download evidence pack/i });
    expect(link).toHaveAttribute("href", `/api${arts[0].download_path}`);
    expect(link.parentElement!).toHaveTextContent(`SHA-256 ${arts[0].sha256}`);
  });

  it("says plainly when the audit copy could not be stored", async () => {
    const exec = decisionExecuted as unknown as Decision;
    const failed = [{ ...arts[0], status: "FAILED", download_path: null, sha256: null, error: "S3 upload failed (AccessDenied)" }];
    mockApi({ ...caseRoutes(findingAwaiting, decisionExecuted), [`GET /decisions/${exec.decision_id}/artifacts`]: failed });
    renderApp(`/cases/risk/${risk.id}`);
    expect(await screen.findByText(/The audit copy could not be saved/i)).toBeInTheDocument();
    expect(screen.getByText(/S3 upload failed \(AccessDenied\)/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download evidence pack/i })).not.toBeInTheDocument();
  });
});

describe("Data & System: readiness and AI failure drill", () => {
  const dataRoutes = {
    "GET /overview": overviewCritical,
    "GET /system/status": { database_status: "UP", seeded: true, simulator_status: "IDLE", detection_engine_status: "IDLE", data_window: null, record_counts: {}, llm_agents_enabled: false },
    "GET /agents": { enabled: true, provider: { provider: "deterministic", model: "rule-based", is_llm: false }, agents: [] },
  };

  it("shows the backend readiness checks without any connection details", async () => {
    mockApi({ ...dataRoutes, "GET /ready": ready });
    renderApp("/data");
    const section = (await screen.findByRole("heading", { name: /system readiness/i })).closest("section")!;
    expect(await within(section).findByText("Ready")).toBeInTheDocument();
    expect(within(section).getByText(new RegExp(`version ${rdy.version}`))).toBeInTheDocument();
    expect(within(section).getByText(/Database schema version/)).toBeInTheDocument();
    expect(within(section).getByText(/Evidence-pack storage/)).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/postgresql|:\/\/|sslmode|arn:aws|localhost|:5432/i); // no connection strings, hosts or ARNs
  });

  it("shows a not-ready answer (HTTP 503) instead of an error page", async () => {
    const notReady = { ...ready, status: "not_ready", checks: { ...rdy.checks, migrations: { ok: false, status: "BEHIND", current: null, head: "x" } } };
    mockApi({ ...dataRoutes, "GET /ready": notReady }, { status: { "GET /ready": 503 } });
    renderApp("/data");
    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText(/Problem: Behind/)).toBeInTheDocument();
  });

  it("switches the AI failure drill through the backend", async () => {
    let mode = "off";
    const { calls } = mockApi({
      ...dataRoutes,
      "GET /ready": () => ({ ...ready, checks: { ...rdy.checks, ai: { ...rdy.checks.ai, outage_drill: mode, status: mode === "off" ? "RULE_BASED" : "OUTAGE_DRILL" } } }),
      "POST /demo/ai-outage": (body: unknown) => { mode = (body as { mode: string }).mode; return { mode, enabled: true }; },
    });
    renderApp("/data");
    const noSubstitute = await screen.findByRole("radio", { name: /AI down, no substitute/i });
    expect(screen.getByRole("radio", { name: /normal/i })).toBeChecked();
    await userEvent.click(noSubstitute);
    expect(calls.find((c) => c.method === "POST")).toEqual({ method: "POST", path: "/demo/ai-outage", body: { mode: "fail" } });
    expect(await screen.findByRole("radio", { name: /AI down, no substitute/i })).toBeChecked();
    expect(await screen.findByText(/Outage drill/)).toBeInTheDocument();
  });

  it("hides the drill when the environment disables demo controls", async () => {
    mockApi({ ...dataRoutes, "GET /ready": { ...ready, demo_controls_enabled: false } });
    renderApp("/data");
    await screen.findByText("Ready");
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});

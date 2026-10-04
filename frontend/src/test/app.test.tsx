// Component/integration tests over the real app routes, using REACT Merchant API responses captured from the
// backend (scripts/capture_ui_fixtures.py). No business numbers are hard-coded: expectations come from the fixtures.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderApp } from "./utils";
import { money } from "../lib/format";
import type { Decision, Evidence, Finding, InventoryRow, Investigation, Merchant, Overview, ProductRow, SupplierRow } from "../lib/types";

import activity from "./fixtures/activity.json";
import agentRun from "./fixtures/agent_run.json";
import decisionAwaiting from "./fixtures/decision_awaiting.json";
import decisionExecuted from "./fixtures/decision_executed.json";
import decisions from "./fixtures/decisions.json";
import evidence from "./fixtures/evidence.json";
import findingAwaiting from "./fixtures/finding_awaiting.json";
import findingDetected from "./fixtures/finding_detected.json";
import findingOpportunity from "./fixtures/finding_opportunity.json";
import findingsOpportunities from "./fixtures/findings_opportunities.json";
import findingsRisks from "./fixtures/findings_risks.json";
import graph from "./fixtures/graph.json";
import inventory from "./fixtures/inventory.json";
import investigation from "./fixtures/investigation.json";
import merchant from "./fixtures/merchant.json";
import merchantTemplates from "./fixtures/merchant_templates.json";
import overviewAfter from "./fixtures/overview_after.json";
import overviewCritical from "./fixtures/overview_critical.json";
import overviewHealthy from "./fixtures/overview_healthy.json";
import products from "./fixtures/products.json";
import suppliers from "./fixtures/suppliers.json";

const risk = findingDetected as unknown as Finding;
const dec = decisionAwaiting as unknown as Decision;
const inv = investigation as unknown as Investigation;
const ev = evidence as unknown as Evidence[];
const critical = overviewCritical as unknown as Overview;

afterEach(() => vi.unstubAllGlobals());

function caseRoutes(finding: unknown, decision: unknown) {
  return {
    "GET /overview": overviewCritical,
    [`GET /findings/risk/${risk.id}`]: finding,
    [`GET /decisions/${dec.decision_id}`]: decision,
    [`GET /investigations/${dec.investigation_id}`]: investigation,
    [`GET /investigations/${dec.investigation_id}/evidence`]: evidence,
    [`GET /investigations/${dec.investigation_id}/graph`]: graph,
    [`GET /agent-runs/${dec.agent_run_id}`]: agentRun,
  };
}

describe("Dashboard", () => {
  it("answers how the shop is doing, what needs attention and what is at stake", async () => {
    mockApi({ "GET /overview": overviewCritical });
    renderApp("/");
    expect(await screen.findByText(/Business status: Critical/i)).toBeInTheDocument();
    expect(screen.getByText(critical.headline)).toBeInTheDocument();
    const attention = screen.getByRole("heading", { name: /needs your attention/i }).closest("section")!;
    for (const f of critical.attention) expect(within(attention).getByText(f.title)).toBeInTheDocument();
    // severity is written out, never colour alone
    expect(within(attention).getAllByText("CRITICAL").length).toBeGreaterThan(0);
    // the ledger total comes from the backend's deterministic impact estimates
    const ledger = screen.getByRole("heading", { name: /what is at stake/i }).closest("section")!;
    expect(within(ledger).getAllByText(money(critical.impact.exposure_total!, "INR")).length).toBeGreaterThan(0);
    expect(screen.getByText(/Inventory health/i)).toBeInTheDocument();
    // synthetic data is always labelled
    expect(screen.getAllByText(/simulated business/i).length).toBeGreaterThan(0);
  });

  it("shows a calm empty state when nothing needs attention", async () => {
    const calm = { ...overviewHealthy, business_status: "HEALTHY", attention: [], counts: { ...overviewHealthy.counts, other: 0 } };
    mockApi({ "GET /overview": calm });
    renderApp("/");
    expect(await screen.findByText(/Business status: Healthy/i)).toBeInTheDocument();
    expect(screen.getByText(/Nothing needs your attention/i)).toBeInTheDocument();
  });

  it("reflects a handled issue after approval", async () => {
    mockApi({ "GET /overview": overviewAfter });
    renderApp("/");
    const handled = (await screen.findByRole("heading", { name: /^Handled$/ })).closest("section")!;
    const done = (overviewAfter as unknown as Overview).addressed[0];
    expect(within(handled).getByText(done.title)).toBeInTheDocument();
    expect(within(handled).getByText(/Done \(simulated\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/Business status: Critical/i)).not.toBeInTheDocument();
  });

  it("shows a loading state instead of a blank screen", async () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderApp("/");
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  it("shows a meaningful error with retry when the server fails", async () => {
    mockApi({ "GET /overview": { detail: "database unavailable" } }, { status: { "GET /overview": 503 } });
    renderApp("/");
    expect(await screen.findByText(/Could not load the dashboard/i)).toBeInTheDocument();
    expect(screen.getAllByText(/database unavailable/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("Findings and opportunities", () => {
  it("lists findings with filters by stock, suppliers and money", async () => {
    mockApi({ "GET /overview": overviewCritical, "GET /findings": findingsRisks });
    renderApp("/findings");
    const risks = findingsRisks as unknown as Finding[];
    expect((await screen.findAllByText(risks[0].title)).length).toBeGreaterThan(0);
    const caseLinks = () => screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("/cases/"));
    expect(caseLinks()).toHaveLength(risks.length);
    await userEvent.click(screen.getByRole("button", { name: /^suppliers$/i }));
    const supplier = risks.filter((f) => f.type === "SUPPLIER_DELAY" || f.type === "SUPPLIER_RELIABILITY");
    expect(caseLinks()).toHaveLength(supplier.length);
    await userEvent.click(screen.getByRole("button", { name: /margin and sales/i }));
    expect(screen.getByText(/Nothing matches this filter/i)).toBeInTheDocument();
  });

  it("explains each opportunity: what, why, value, what could be done", async () => {
    mockApi({ "GET /overview": overviewCritical, "GET /findings": findingsOpportunities });
    renderApp("/opportunities");
    const opp = findingOpportunity as unknown as Finding;
    expect(await screen.findByText(opp.title)).toBeInTheDocument();
    expect(screen.getAllByText(/Why REACT flagged it/i).length).toBe((findingsOpportunities as unknown[]).length);
    expect(screen.getAllByText(/What could be done/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(money(opp.impact!.value, "INR")).length).toBeGreaterThan(0);
  });
});

describe("Products, inventory and suppliers", () => {
  it("shows product sales, margin and stock, with search", async () => {
    mockApi({ "GET /overview": overviewCritical, "GET /products": products });
    renderApp("/products");
    const rows = products as unknown as ProductRow[];
    const flagged = rows.find((p) => p.findings.length)!;
    expect(await screen.findByText(flagged.name)).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/search by name or sku/i), flagged.sku);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(2); // header + the matching product
    expect(within(table).getByText(flagged.name)).toBeInTheDocument();
  });

  it("filters stock by status", async () => {
    mockApi({ "GET /overview": overviewCritical, "GET /inventory": inventory });
    renderApp("/inventory");
    const rows = inventory as unknown as InventoryRow[];
    const out = rows.filter((r) => r.status === "OUT_OF_STOCK");
    await userEvent.click(await screen.findByRole("button", { name: new RegExp(`^Out of stock \\(${out.length}\\)$`) }));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(out.length + 1);
  });

  it("shows supplier reliability and links a supplier finding", async () => {
    mockApi({ "GET /overview": overviewCritical, "GET /suppliers": suppliers });
    renderApp("/suppliers");
    const rows = suppliers as unknown as SupplierRow[];
    const late = rows.find((s) => s.findings.length)!;
    const row = (await screen.findByText(late.name)).closest("tr")!;
    expect(within(row).getByText(`${late.late_of_last_6} of ${late.deliveries_considered}`)).toBeInTheDocument();
    expect(within(row).getByRole("link")).toHaveAttribute("href", expect.stringMatching(/^\/cases\//));
  });

  it("generates a demo business from the merchant's description", async () => {
    const m = merchant as unknown as Merchant;
    const { calls } = mockApi({ "GET /overview": overviewCritical, "GET /merchant": merchant, "GET /merchant/templates": merchantTemplates, "POST /merchant/setup": merchant });
    renderApp("/setup");
    const name = await screen.findByLabelText(/business name/i);
    expect(name).toHaveValue(m.profile!.business_name);
    await userEvent.clear(name);
    await userEvent.type(name, "Test Mobiles");
    await userEvent.click(screen.getByRole("button", { name: /generate demo business/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")).toMatchObject({ path: "/merchant/setup", body: { business_name: "Test Mobiles", city: m.profile!.city } });
  });
});

describe("Investigation (case) page", () => {
  it("offers to investigate a new finding and runs the REACT workflow", async () => {
    let current: unknown = findingDetected;
    const { calls } = mockApi({
      ...caseRoutes(() => current, decisionAwaiting),
      "POST /react/run": () => { current = findingAwaiting; return decisionAwaiting; },
    });
    renderApp(`/cases/risk/${risk.id}`);
    await userEvent.click(await screen.findByRole("button", { name: /investigate with react/i }));
    expect(await screen.findByRole("heading", { name: /what should i do/i })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")).toEqual({ method: "POST", path: "/react/run", body: { risk_id: risk.id } });
  });

  it("explains what happened, the likely cause, why, what does not fit and the cost", async () => {
    mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}`);
    expect(await screen.findByRole("heading", { name: dec.hypothesis_label! })).toBeInTheDocument();
    // key signals chosen by the backend, with their stored values
    for (const k of inv.key_signals!.slice(0, 3)) expect(screen.getAllByText(k.label).length).toBeGreaterThan(0);
    expect(screen.getByText(/What does not fit/i)).toBeInTheDocument();
    expect(screen.getAllByText(money(inv.impact.estimated_total!, "INR")).length).toBeGreaterThan(0);
    // formulas are hidden by default but one click away
    expect(screen.getAllByText("View calculation").length).toBeGreaterThan(0);
  });

  it("answers WHY in plain language and links to the evidence", async () => {
    mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}`);
    await userEvent.click(await screen.findByRole("button", { name: /why does react think this/i }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(new RegExp(`Why: ${dec.hypothesis_label}`, "i"))).toBeInTheDocument();
    expect(within(dialog).getByText(/strongly supported/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /see the source records/i }));
    expect(await screen.findByRole("heading", { name: /How REACT reached its conclusion/i })).toBeInTheDocument();
  });

  it("renders the evidence with direction, source and an honest baseline", async () => {
    mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}?tab=evidence`);
    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("Supports").length).toBeGreaterThan(0);
    expect(within(table).getAllByText("Supplier deliveries").length).toBeGreaterThan(0);
    // REACT shows the evidence against an explanation too: pick one the records argue against
    const against = ev.find((e) => e.contradicts)!;
    const hyp = inv.hypotheses.find((h) => h.hypothesis_id === against.hypothesis_id)!;
    await userEvent.selectOptions(screen.getByLabelText("Explanation"), hyp.hypothesis_id);
    expect(within(screen.getByRole("table")).getAllByText("Against").length).toBeGreaterThan(0);
    // regression: a % change shows "vs normal", never its raw baseline with a % sign
    const pctItem = ev.find((e) => e.hypothesis_id === hyp.hypothesis_id && e.unit === "%")!;
    const row = within(screen.getByRole("table")).getAllByRole("row").find((r) => r.textContent?.includes(pctItem.claim))!;
    expect(row.textContent).toContain("vs normal");
    await userEvent.click(screen.getByRole("button", { name: /^Against$/ }));
    expect(within(screen.getByRole("table")).queryByText("Supports")).not.toBeInTheDocument();
  });

  it("shows technical details only on request: scores, trace, tool calls", async () => {
    mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}`);
    await screen.findByRole("heading", { name: dec.hypothesis_label! });
    expect(screen.queryByRole("heading", { name: "Evidence Support Score" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /technical details/i }));
    expect(await screen.findByRole("heading", { name: "Evidence Support Score" })).toBeInTheDocument();
    expect(await screen.findByText(/no language model was called/i)).toBeInTheDocument();
  });
});

describe("Decision and approval", () => {
  it("shows what REACT worked out for the recommended action", async () => {
    mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}`);
    const panel = (await screen.findByText(/REACT recommends/i)).closest("section")!;
    expect(within(panel).getByText(new RegExp(`Reorder now: ${dec.target_name}`))).toBeInTheDocument();
    expect(within(panel).getByText(/What REACT worked out/i)).toBeInTheDocument();
    expect(within(panel).getByText(/rule-based recommendation/i)).toBeInTheDocument();
  });

  it("requires a name, then approves through the backend and shows the simulated result", async () => {
    const executed = decisionExecuted as unknown as Decision;
    const { calls } = mockApi({ ...caseRoutes(findingAwaiting, decisionAwaiting), [`POST /decisions/${dec.decision_id}/approve`]: decisionExecuted });
    renderApp(`/cases/risk/${risk.id}`);
    const approve = await screen.findByRole("button", { name: /^approve$/i });
    await userEvent.click(approve);
    expect(screen.getByRole("alert")).toHaveTextContent(/enter your name/i);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.type(screen.getByLabelText(/your name/i), "Test reviewer");
    await userEvent.click(approve);
    expect(await screen.findByText(/Action completed/i)).toBeInTheDocument();
    expect(screen.getByText(/no purchase order, price change, message or payment was sent/i)).toBeInTheDocument();
    const ref = String((executed.actions[0].result as Record<string, unknown>).task_id);
    expect(ref).toMatch(/^SIM-/);
    expect(screen.getByText(ref)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/decisions/${dec.decision_id}/approve`);
    expect(post.body).toMatchObject({ reviewed_by: "Test reviewer", execute: true });
  });

  it("requires a reason to reject", async () => {
    const { calls } = mockApi(caseRoutes(findingAwaiting, decisionAwaiting));
    renderApp(`/cases/risk/${risk.id}`);
    await userEvent.type(await screen.findByLabelText(/your name/i), "Test reviewer");
    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm rejection/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/reason/i);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("surfaces a backend refusal instead of pretending the approval worked", async () => {
    mockApi({ ...caseRoutes(findingAwaiting, decisionAwaiting), [`POST /decisions/${dec.decision_id}/approve`]: { detail: "decision cannot move from EXECUTED to APPROVED" } }, { status: { [`POST /decisions/${dec.decision_id}/approve`]: 409 } });
    renderApp(`/cases/risk/${risk.id}`);
    await userEvent.type(await screen.findByLabelText(/your name/i), "Test reviewer");
    await userEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot move from EXECUTED/);
    expect(screen.queryByText(/Action completed/i)).not.toBeInTheDocument();
  });

  it("groups decisions on the Decisions page", async () => {
    mockApi({ "GET /overview": overviewAfter, "GET /decisions?limit=200": decisions });
    renderApp("/decisions");
    expect(await screen.findByRole("heading", { name: /^Approved/ })).toBeInTheDocument();
    expect(screen.getByText(/No decisions are waiting for you/i)).toBeInTheDocument();
  });
});

describe("Honesty about capabilities", () => {
  it("marks document upload as coming soon and says no language model is connected", async () => {
    mockApi({
      "GET /overview": overviewCritical,
      "GET /merchant": merchant,
      "GET /system/status": { database_status: "UP", seeded: true, simulator_status: "IDLE", detection_engine_status: "IDLE", data_window: null, record_counts: {}, llm_agents_enabled: false },
      "GET /agents": { enabled: true, provider: { provider: "deterministic", model: "rule-based", is_llm: false }, agents: [] },
    });
    renderApp("/data");
    expect(await screen.findByText(/Coming soon/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload \(not available\)/i })).toBeDisabled();
    expect(await screen.findByText(/No language model is connected/i)).toBeInTheDocument();
    expect(await screen.findByText(/simulated business/i, { selector: "strong" })).toBeInTheDocument();
  });

  it("shows the activity feed from the backend", async () => {
    mockApi({ "GET /overview": overviewAfter, "GET /activity?limit=100": activity });
    renderApp("/activity");
    expect(await screen.findByText(/Simulated action completed/i)).toBeInTheDocument();
    expect(screen.getByText(/Scenario loaded: Stockout crisis/i)).toBeInTheDocument();
  });

  it("handles an unknown finding", async () => {
    mockApi({ "GET /overview": overviewCritical });
    renderApp(`/cases/risk/00000000-0000-0000-0000-000000000000`);
    await waitFor(() => expect(screen.getByText(/This finding does not exist/i)).toBeInTheDocument());
  });
});

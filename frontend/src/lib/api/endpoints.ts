// One function per backend endpoint used by the UI. Paths match app/api/*.py.
import { API_BASE, get, getAllowing, post } from "./client";
import type {
  ActivityItem,
  AgentRoster,
  AgentRun,
  Artifact,
  Decision,
  DecisionSummary,
  DemoResult,
  DemoScenario,
  Evidence,
  EvidenceGraph,
  Finding,
  FindingKind,
  Investigation,
  InventoryRow,
  Merchant,
  MerchantProfile,
  MerchantTemplate,
  ProductRow,
  SupplierRow,
  ValueMetrics,
  Overview,
  OutageMode,
  Page,
  Readiness,
  ScanResult,
  SystemStatus,
} from "../types";

const subjectKey = (kind: FindingKind) => `${kind}_id`;

export const api = {
  overview: () => get<Overview>("/overview"),
  findings: (kind?: "risk_all" | "opportunity" | "incident" | "risk", includeClosed = false) =>
    get<Finding[]>(`/findings?${new URLSearchParams({ ...(kind ? { kind } : {}), include_closed: String(includeClosed) })}`),
  finding: (kind: FindingKind, id: string) => get<Finding>(`/findings/${kind}/${id}`),
  activity: (limit = 60) => get<ActivityItem[]>(`/activity?limit=${limit}`),
  scan: () => post<ScanResult>("/scan"),
  demoScenarios: () => get<DemoScenario[]>("/demo/scenarios"),
  runDemo: (scenario: string) => post<DemoResult>("/demo/run", { scenario }),

  investigation: (id: string) => get<Investigation>(`/investigations/${id}`),
  evidence: (id: string) => get<Evidence[]>(`/investigations/${id}/evidence`),
  graph: (id: string) => get<EvidenceGraph>(`/investigations/${id}/graph`),
  agentRun: (id: string) => get<AgentRun>(`/agent-runs/${id}`),

  /** Full REACT workflow for a finding: agents → investigation → evidence → impact → decision (awaiting approval). */
  investigate: (kind: FindingKind, id: string) => post<Decision>("/react/run", { [subjectKey(kind)]: id }),
  decision: (id: string) => get<Decision>(`/decisions/${id}`),
  decisions: (status?: string) => get<Page<DecisionSummary>>(`/decisions?limit=200${status ? `&status=${status}` : ""}`),
  approve: (id: string, reviewedBy: string, reason?: string) => post<Decision>(`/decisions/${id}/approve`, { reviewed_by: reviewedBy, reason: reason || null, execute: true }),
  reject: (id: string, reviewedBy: string, reason: string) => post<Decision>(`/decisions/${id}/reject`, { reviewed_by: reviewedBy, reason }),
  execute: (id: string, executedBy: string) => post<Decision>(`/decisions/${id}/execute`, { executed_by: executedBy }),

  // REACT Merchant
  merchant: () => get<Merchant>("/merchant"),
  merchantTemplates: () => get<MerchantTemplate[]>("/merchant/templates"),
  setupMerchant: (profile: MerchantProfile) => post<Merchant>("/merchant/setup", profile),
  products: () => get<ProductRow[]>("/products"),
  inventory: () => get<InventoryRow[]>("/inventory"),
  suppliers: () => get<SupplierRow[]>("/suppliers"),
  valueMetrics: () => get<ValueMetrics>("/metrics/value"),

  systemStatus: () => get<SystemStatus>("/system/status"),
  agents: () => get<AgentRoster>("/agents"),

  /** Phase 6: readiness answers 200 (ready) or 503 (not ready) with the same body; both are shown. */
  ready: async () => (await getAllowing<Readiness>("/ready", [503])).body,
  artifacts: (decisionId: string) => get<Artifact[]>(`/decisions/${decisionId}/artifacts`),
  outage: () => get<{ mode: OutageMode; enabled: boolean }>("/demo/ai-outage"),
  setOutage: (mode: OutageMode) => post<{ mode: OutageMode; enabled: boolean }>("/demo/ai-outage", { mode }),
};

/** Browser URL for an artifact download (same origin: Vite proxy locally, CloudFront /api/* on AWS). */
export const downloadUrl = (downloadPath: string) => `${API_BASE}${downloadPath}`;

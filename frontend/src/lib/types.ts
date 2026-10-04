// Types mirror the backend Pydantic response schemas (app/schemas/*.py, app/decisions/schemas.py).
// They describe what the API returns; nothing here is invented client-side.

export type FindingKind = "incident" | "risk" | "opportunity";
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type Confidence = "HIGH" | "MEDIUM" | "LOW";
export type BusinessStatus = "HEALTHY" | "NEEDS_ATTENTION" | "CRITICAL";
export type Stage =
  | "DETECTED"
  | "INVESTIGATED"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "ACTION_COMPLETED"
  | "REJECTED"
  | "INSUFFICIENT_EVIDENCE"
  | "FAILED";

export interface EntityRef {
  type: string | null;
  id: string | null;
  name: string | null;
  code: string | null;
}

export interface ImpactSummary {
  value: number;
  currency: string;
  description: string | null;
  formula: string | null;
  basis: string;
}

export interface WorkflowRef {
  id: string;
  status: string;
  selected_hypothesis?: string | null;
  selected_label?: string | null;
  evidence_support_score?: number | null;
  result_status?: string | null;
  action_type?: string | null;
  decision_readiness?: string | null;
  summary?: string | null;
}

export interface Finding {
  kind: FindingKind;
  id: string;
  type: string;
  title: string;
  description: string;
  status: string;
  detected_at: string;
  severity: Severity | null;
  confidence: Confidence | null;
  entity: EntityRef;
  location: string | null;
  impact: ImpactSummary | null;
  evidence_count: number;
  stage: Stage;
  investigation: WorkflowRef | null;
  agent_run: WorkflowRef | null;
  decision: WorkflowRef | null;
  signal?: string | null;
  supporting_metrics?: Record<string, unknown> | null;
}

export interface ActivityItem {
  at: string;
  kind: "simulation" | "scan" | "investigation" | "decision";
  title: string;
  detail: string | null;
  status: string | null;
  link: { kind: FindingKind; id: string } | null;
}

export interface Overview {
  company: { name: string | null; industry: string | null; currency: string };
  as_of: string | null;
  data_source: string;
  scanned: boolean;
  business_status: BusinessStatus;
  headline: string;
  counts: { critical: number; high: number; other: number; addressed: number; opportunities: number; pending_decisions: number };
  attention: Finding[];
  addressed: Finding[];
  opportunities: Finding[];
  impact: {
    exposure_total: number | null;
    exposure_count: number;
    opportunity_total: number | null;
    opportunity_count: number;
    currency: string;
    note: string;
  };
  metrics: {
    period_days: number;
    revenue: number;
    revenue_change_pct: number | null;
    gross_margin: number;
    orders: number;
    currency: string;
    line_utilization: { code: string; name: string; avg_utilization: number; status: string }[];
  } | null;
  recent_activity: ActivityItem[];
  domain?: string;
  business_health?: BusinessHealth | null;
  value?: ValueMetrics | null;
  merchant?: { business_name: string | null; business_type: string | null; city: string | null; state: string | null; stores: StoreRef[]; scenario: string | null; is_synthetic: boolean } | null;
}

// ---- REACT Merchant -------------------------------------------------------------------------------------
export interface StoreRef { id: string; code: string; name: string; locality: string; region: string; type: string }

export interface PeriodTotals {
  revenue: number;
  gross_margin: number;
  gross_margin_rate: number | null;
  orders: number;
  units: number;
  average_order_value: number | null;
  discount_rate: number | null;
}

export interface BusinessHealth extends PeriodTotals {
  period: { label: string; start: string; end: string };
  previous: PeriodTotals;
  revenue_change_pct: number | null;
  gross_margin_change_pct: number | null;
  orders_change_pct: number | null;
  revenue_vs_normal_week_pct: number | null;
  normal_weekly_revenue: number;
  inventory: { healthy_pct: number | null; products_considered: number; healthy: number; short_of_stock: number; overstocked: number; rule: string };
  currency: string;
  basis: string;
}

export interface ValueMetrics {
  potential_loss_identified: number;
  opportunity_identified: number;
  inventory_capital_at_risk: number;
  issues_detected: number;
  issues_open: number;
  issues_resolved: number;
  stockout_risks_detected: number;
  supplier_risks_detected: number;
  opportunities_detected: number;
  actions_recommended: number;
  actions_approved: number;
  actions_rejected: number;
  simulated_actions_executed: number;
  revenue_protected_estimate: number;
  margin_protected_estimate: number;
  opportunity_pursued_estimate: number;
  currency: string;
}

export interface FindingFlag { kind: FindingKind; id: string; type: string; severity: Severity | null; title: string }

export interface ProductRow {
  id: string;
  sku: string;
  name: string;
  category: string;
  brand: string | null;
  selling_price: number;
  unit_cost: number;
  mrp: number | null;
  margin_rate_at_price: number | null;
  units_28d: number | null;
  revenue_28d: number | null;
  gross_margin_28d: number | null;
  demand_change_pct: number | null;
  stock_on_hand: number | null;
  merchant_days_of_cover: number | null;
  supplier: string | null;
  findings: FindingFlag[];
}

export type StockStatus = "OUT_OF_STOCK" | "LOW" | "OVERSTOCKED" | "HEALTHY";

export interface InventoryRow {
  id: string;
  product_id: string | null;
  item_name: string;
  sku: string | null;
  category: string | null;
  store: string | null;
  quantity_available: number;
  on_order: number | null;
  reorder_point: number;
  avg_daily_consumption: number;
  days_of_cover: number;
  stock_value: number | null;
  open_purchase_orders: number | null;
  overdue_purchase_orders: number | null;
  supplier: string | null;
  status: StockStatus | null;
  findings: FindingFlag[];
}

export interface SupplierRow {
  id: string;
  name: string;
  component_type: string;
  location: string;
  baseline_lead_time_days: number;
  recent_lead_days: number | null;
  normal_lead_days: number | null;
  on_time_rate_recent: number | null;
  on_time_rate_normal: number | null;
  late_of_last_6: number | null;
  deliveries_considered: number | null;
  open_purchase_orders: number | null;
  overdue_purchase_orders: number | null;
  products: number | null;
  revenue_28d: number | null;
  findings: FindingFlag[];
}

export interface MerchantProfile { business_name: string; business_type: string; city: string; state: string; locality: string; store_count: number; categories: string[] }

export interface Merchant {
  domain: string;
  business_name: string | null;
  business_type: string | null;
  template: string;
  city: string | null;
  state: string | null;
  profile: MerchantProfile | null;
  stores: StoreRef[];
  categories: { id: string; code: string; name: string; target_margin: number }[];
  counts: { products: number; suppliers: number; customers: number; orders: number };
  data_window: { start: string; end: string; seed: number; generator_version: string } | null;
  scenario: string | null;
  is_synthetic: boolean;
}

export interface MerchantTemplate { key: string; label: string; categories: { code: string; name: string; description: string }[] }

export interface KeySignal {
  signal: string;
  label: string;
  value: number | boolean | null;
  unit: string | null;
  display: string;
  direction: "up" | "down" | "flat" | "none";
  baseline_display: string | null;
  claim: string;
  source_type: string;
  record_count: number;
  condition: string;
}

/** One deterministic impact-engine result (app/impact/engine.py ImpactResult.as_dict). */
export interface ImpactResult {
  formula: string;
  inputs: Record<string, number | string | null>;
  value: number;
  unit: string;
  description: string;
  currency: string;
  is_estimate: boolean;
  basis: string;
  components: Record<string, number>;
}

/** investigation.impact / decision.expected_impact (app/investigation/analysis.py). Keys vary by subject kind. */
export interface ImpactAnalysis {
  estimated_total: number | null;
  total_includes?: string[];
  currency?: string;
  wording?: string;
  note?: string;
  operational_constraints?: { type: string; detail: string; signal: string }[];
  potential_additional_demand?: { units_per_day: number; units_over_horizon: number; horizon_days: number; recent_daily_units: number; previous_daily_units: number; basis: string };
  [key: string]: unknown;
}

export interface Hypothesis {
  hypothesis_id: string;
  type: string;
  label?: string | null;
  rank: number;
  description: string;
  triggered_signals: unknown[];
  initial_confidence: number;
  evidence_support_score: number | null;
  supporting_count: number;
  contradicting_count: number;
  status: string;
  score_breakdown: Record<string, unknown>;
}

export interface Evidence {
  evidence_id: string;
  hypothesis_id: string | null;
  signal: string;
  source_type: string;
  source_id: string | null;
  source_ids: string[];
  timestamp: string | null;
  claim: string;
  value: number | null;
  unit: string | null;
  baseline_value: number | null;
  relevance: number;
  supports: boolean;
  contradicts: boolean;
  evidence_support_score: number;
  score_breakdown: Record<string, number>;
  details: Record<string, unknown>;
}

export interface Conclusion {
  kind?: string;
  selected_hypothesis: string | null;
  selected_label?: string;
  evidence_support_score: number | null;
  support_label: string;
  determined: boolean;
  statement: string;
  contributing_factor?: string | null;
  recommended_next_step?: string;
  alternatives?: { hypothesis: string; label: string; evidence_support_score: number; support_label: string }[];
}

export interface TraceEntry {
  sequence: number;
  timestamp: string;
  step: string;
  message: string;
  details: Record<string, unknown>;
  duration_ms: number | null;
}

export interface Investigation {
  investigation_id: string;
  subject_type: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  summary: string | null;
  confidence: number | null;
  confidence_label: string | null;
  conclusion: Conclusion;
  impact: ImpactAnalysis;
  hypotheses: Hypothesis[];
  evidence_count: number;
  trace: TraceEntry[];
  key_signals?: KeySignal[];
}

export interface GraphNode {
  id: string;
  kind: string;
  label: string | null;
  supports?: boolean;
  evidence_support_score?: number;
  rank?: number;
  source_type?: string;
}

export interface EvidenceGraph {
  investigation_id: string;
  graph: { nodes: GraphNode[]; edges: { from: string; to: string; type: string }[]; counts: Record<string, number> };
}

export interface AgentTraceEntry {
  trace_id: string;
  sequence: number;
  timestamp: string;
  agent_name: string;
  event_type: string;
  state: string | null;
  tool_name: string | null;
  evidence_ids: string[];
  hypothesis_ids: string[];
  summary: string;
  duration_ms: number | null;
  status: string;
  error: string | null;
}

export interface AgentToolCall {
  tool_call_id: string;
  sequence: number;
  agent_name: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  purpose: string | null;
  status: string;
  permitted: boolean;
  duration_ms: number | null;
  result_summary: { count?: number | null };
  error: string | null;
}

export interface AgentRun {
  agent_run_id: string;
  status: string;
  result_status: string | null;
  provider: string;
  model: string | null;
  llm_used: boolean;
  fallback_used: boolean;
  tool_calls_count: number;
  llm_calls_count: number;
  trace: AgentTraceEntry[];
  tool_calls: AgentToolCall[];
}

export interface Approval {
  approval_id: string;
  decision_id: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";
  requested_at: string;
  reviewed_at: string | null;
  reviewed_by: string | null;
  reason: string | null;
}

export interface ActionExecution {
  action_id: string;
  decision_id: string;
  approval_id: string | null;
  action_type: string;
  target_type: string | null;
  target_id: string | null;
  target_name: string | null;
  status: "PROPOSED" | "APPROVED" | "REJECTED" | "EXECUTING" | "COMPLETED" | "FAILED";
  simulated: boolean;
  parameters?: Record<string, unknown>;
  result: Record<string, unknown>;
  executed_by: string | null;
  completed_at: string | null;
  error: string | null;
  transitions: { at: string; to: string; by: string }[];
}

export type DecisionStatus =
  | "DECISION_READY"
  | "INSUFFICIENT_EVIDENCE"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXECUTING"
  | "EXECUTED"
  | "FAILED";

export interface DecisionSummary {
  decision_id: string;
  agent_run_id: string | null;
  investigation_id: string | null;
  subject_type: "INCIDENT" | "RISK" | "OPPORTUNITY";
  incident_id: string | null;
  opportunity_id: string | null;
  risk_id: string | null;
  status: DecisionStatus;
  decision_readiness: "READY" | "NEEDS_REVIEW" | "INSUFFICIENT_EVIDENCE";
  action_type: string | null;
  target_type: string | null;
  target_id: string | null;
  target_name: string | null;
  priority: string;
  summary: string;
  recommendation: string;
  hypothesis_type: string | null;
  hypothesis_label?: string | null;
  evidence_support_score: number | null;
  requires_human_approval: boolean;
  provider: string | null;
  llm_used: boolean;
  fallback_used: boolean;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Decision extends DecisionSummary {
  problem_statement: string;
  rationale: string;
  required_actions: string[];
  alternatives: { action_type: string; summary: string; tradeoff: string }[];
  risks: string[];
  assumptions: string[];
  evidence_ids: string[];
  contradicting_evidence_ids: string[];
  hypothesis_id: string | null;
  expected_impact: ImpactAnalysis;
  validation: { passed: boolean; checks: { check: string; passed: boolean; detail?: unknown }[] };
  timeline: { at: string; event: string; actor: string; to_status?: string; reason?: string }[];
  approvals: Approval[];
  actions: ActionExecution[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DemoResult {
  scenario: string;
  label: string;
  story: string;
  simulation_summary: string | null;
  detected: { incidents: number; risks: number; opportunities: number };
  featured: { kind: FindingKind; id: string; title: string } | null;
}

export interface DemoScenario {
  scenario: string;
  label: string;
  story: string;
}

export interface ScanResult {
  incidents_created: number;
  risks_created: number;
  opportunities_created: number;
}

export interface SystemStatus {
  database_status: string;
  seeded: boolean;
  simulator_status: string;
  detection_engine_status: string;
  data_window: { seed: number; data_start: string; data_end: string } | null;
  record_counts: Record<string, number>;
  llm_agents_enabled: boolean;
}

export interface AgentRoster {
  enabled: boolean;
  provider: { provider: string; model?: string; is_llm?: boolean; error?: string };
  agents: { agent: string; responsibility: string; allowed_tools: string[] }[];
}

// ---- Phase 6 -----------------------------------------------------------------------------------
export type OutageMode = "off" | "fallback" | "fail";

export interface ReadinessCheck {
  ok: boolean;
  status: string;
  detail?: string;
  backend?: string;
  [key: string]: unknown;
}

export interface Readiness {
  status: "ready" | "not_ready";
  app: string;
  version: string;
  environment: string;
  demo_controls_enabled: boolean;
  checks: {
    database: ReadinessCheck;
    migrations?: ReadinessCheck;
    data?: ReadinessCheck;
    storage: ReadinessCheck;
    ai: ReadinessCheck & { provider?: string; model?: string; is_llm?: boolean; fallback_to_deterministic?: boolean; outage_drill?: OutageMode; llm_calls_last_hour?: number; llm_hourly_call_budget?: number };
  };
}

export interface Artifact {
  id: string;
  kind: string;
  decision_id: string | null;
  trigger: string;
  status: "STORED" | "FAILED" | string;
  storage_backend: string;
  content_type: string;
  size_bytes: number | null;
  sha256: string | null;
  error: string | null;
  created_at: string;
  download_path: string | null;
}

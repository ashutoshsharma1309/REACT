import type { Confidence, Severity, Stage } from "../../lib/types";
import { STAGE_LABEL } from "../../lib/language";
import { Icon } from "./Icon";

const SEVERITY_TONE: Record<Severity, string> = { CRITICAL: "critical", HIGH: "high", MEDIUM: "medium", LOW: "low" };
const SEVERITY_ICON: Record<Severity, "alert" | "warn" | "info" | "dot"> = { CRITICAL: "alert", HIGH: "warn", MEDIUM: "info", LOW: "dot" };

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`badge badge-${SEVERITY_TONE[severity]}`}>
      <Icon name={SEVERITY_ICON[severity]} />
      {severity}
    </span>
  );
}

const CONFIDENCE_WORDS: Record<Confidence, string> = { HIGH: "Strong signal", MEDIUM: "Moderate signal", LOW: "Weak signal" };

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`badge ${confidence === "HIGH" ? "badge-good" : confidence === "MEDIUM" ? "badge-info" : "badge-neutral"}`}>
      <Icon name="trend" />
      {CONFIDENCE_WORDS[confidence]}
    </span>
  );
}

const STAGE_TONE: Record<Stage, string> = {
  DETECTED: "badge-neutral",
  INVESTIGATED: "badge-info",
  AWAITING_APPROVAL: "badge-high",
  APPROVED: "badge-info",
  ACTION_COMPLETED: "badge-good",
  REJECTED: "badge-neutral",
  INSUFFICIENT_EVIDENCE: "badge-medium",
  FAILED: "badge-critical",
};
const STAGE_ICON: Record<Stage, "search" | "check" | "clock" | "x" | "warn" | "dot"> = {
  DETECTED: "search",
  INVESTIGATED: "check",
  AWAITING_APPROVAL: "clock",
  APPROVED: "check",
  ACTION_COMPLETED: "check",
  REJECTED: "x",
  INSUFFICIENT_EVIDENCE: "warn",
  FAILED: "x",
};

export function StageBadge({ stage }: { stage: Stage }) {
  return (
    <span className={`badge ${STAGE_TONE[stage]}`}>
      <Icon name={STAGE_ICON[stage]} />
      {STAGE_LABEL[stage]}
    </span>
  );
}

export function SimulatedBadge() {
  return (
    <span className="badge badge-sim" title="REACT simulated this action. Nothing changed in the real world.">
      <Icon name="flask" />
      Simulated
    </span>
  );
}

export function SyntheticBadge() {
  // a dashed 'stamp' so it reads as a label on the page, not as a status
  return (
    <span className="stamp" title="This is a simulated business: every sale, product, supplier and customer is synthetic.">
      <Icon name="doc" />
      Simulated business
    </span>
  );
}

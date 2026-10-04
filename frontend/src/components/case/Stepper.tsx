import type { Stage } from "../../lib/types";
import { Icon } from "../common/Icon";

const STEPS = ["Detected", "Investigated", "Recommendation", "Your approval", "Action (simulated)"] as const;

/** Where this finding is in the REACT loop. Derived from the backend stage only. */
function progress(stage: Stage): { done: number; label?: string } {
  switch (stage) {
    case "DETECTED": return { done: 1 };
    case "INVESTIGATED": return { done: 2 };
    case "INSUFFICIENT_EVIDENCE": return { done: 2, label: "Not enough evidence to recommend an action" };
    case "FAILED": return { done: 1, label: "The investigation failed" };
    case "AWAITING_APPROVAL": return { done: 3 };
    case "REJECTED": return { done: 3, label: "You rejected the recommendation" };
    case "APPROVED": return { done: 4 };
    case "ACTION_COMPLETED": return { done: 5 };
  }
}

export function Stepper({ stage }: { stage: Stage }) {
  const { done, label } = progress(stage);
  return (
    <div>
      <ol className="stepper" aria-label="REACT workflow progress">
        {STEPS.map((s, i) => {
          const state = i < done ? "done" : i === done ? "current" : "todo";
          return (
            <li key={s} className="step" data-state={state} aria-current={state === "current" ? "step" : undefined}>
              <span className="step-bar" aria-hidden="true" />
              <span className="step-label">
                {state === "done" && <span style={{ width: 12, display: "inline-flex" }}><Icon name="check" /></span>}
                {s}
                <span className="sr-only">{state === "done" ? " (done)" : state === "current" ? " (next)" : ""}</span>
              </span>
            </li>
          );
        })}
      </ol>
      {label && <p className="muted small" style={{ marginTop: 8 }}>{label}</p>}
    </div>
  );
}

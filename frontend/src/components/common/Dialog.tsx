import { useEffect, useId, useRef, type ReactNode } from "react";
import { Icon } from "./Icon";

/** Accessible modal: labelled, closes on Escape and backdrop click, focus moves in and returns. */
export function Dialog({ title, subtitle, onClose, children }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode }) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1} ref={ref}>
        <div className="dialog-head">
          <div>
            <h2 id={id}>{title}</h2>
            {subtitle && <p className="muted small" style={{ marginTop: 4 }}>{subtitle}</p>}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <span style={{ width: 16, display: "inline-flex" }}><Icon name="x" /></span>
          </button>
        </div>
        <div className="dialog-body">{children}</div>
      </div>
    </div>
  );
}

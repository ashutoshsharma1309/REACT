import type { ReactNode } from "react";
import type { ApiError } from "../../lib/api/client";
import { Icon } from "./Icon";

export function LoadingState({ title = "Loading", detail }: { title?: string; detail?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <h3>{title}</h3>
      {detail && <p className="small">{detail}</p>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Something went wrong" }: { error: ApiError | Error | string; onRetry?: () => void; title?: string }) {
  const message = typeof error === "string" ? error : error.message;
  return (
    <div className="state" role="alert">
      <span className="state-error" style={{ width: 28 }}>
        <Icon name="warn" />
      </span>
      <h3>{title}</h3>
      <p className="small">{message}</p>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          <Icon name="refresh" /> Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, detail, action }: { title: string; detail?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {detail && <p className="small">{detail}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

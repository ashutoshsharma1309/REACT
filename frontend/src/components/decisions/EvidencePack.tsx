import { api, downloadUrl } from "../../lib/api/endpoints";
import { dateTime } from "../../lib/format";
import { useApi } from "../../lib/useApi";
import { Icon } from "../common/Icon";

const FINAL = new Set(["APPROVED", "EXECUTED", "REJECTED", "FAILED"]);

/** Audit copy written by the backend when a person approves or rejects (Phase 6). Shows exactly what the server
 *  recorded, including a failed upload; a failure never changes the decision itself. */
export function EvidencePack({ decisionId, status }: { decisionId: string; status: string }) {
  const arts = useApi(FINAL.has(status) ? () => api.artifacts(decisionId) : null, [decisionId, status]);
  const list = arts.data ?? [];
  if (!FINAL.has(status) || arts.error || list.length === 0) return null;
  return (
    <div className="stack" style={{ gap: 6 }} aria-label="Audit record">
      {list.map((a) =>
        a.status === "STORED" && a.download_path ? (
          <div key={a.id} className="callout">
            <Icon name="shield" />
            <div>
              <strong>Audit record saved.</strong> REACT stored an evidence pack of this decision (evidence, figures, approval and trace) on {dateTime(a.created_at)}.{" "}
              <a href={downloadUrl(a.download_path)} download>Download evidence pack</a>
              <div className="muted small mono" style={{ overflowWrap: "anywhere" }}>SHA-256 {a.sha256}</div>
            </div>
          </div>
        ) : (
          <div key={a.id} className="callout callout-warn" role="status">
            <Icon name="warn" />
            <div><strong>The audit copy could not be saved.</strong> {a.error ?? "Storage failed."} The decision itself is recorded; the copy can be regenerated later.</div>
          </div>
        ),
      )}
    </div>
  );
}

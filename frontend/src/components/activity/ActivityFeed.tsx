import { Link } from "react-router-dom";
import type { ActivityItem } from "../../lib/types";
import { timeAgo } from "../../lib/format";
import { Icon } from "../common/Icon";

const ICON = { simulation: "flask", scan: "search", investigation: "list", decision: "check" } as const;

export function ActivityFeed({ items, emptyText = "Nothing has happened yet." }: { items: ActivityItem[]; emptyText?: string }) {
  if (!items.length) return <p className="muted small">{emptyText}</p>;
  return (
    <ul className="feed">
      {items.map((a, i) => (
        <li key={`${a.at}-${i}`}>
          <span className="feed-icon" aria-hidden="true"><Icon name={ICON[a.kind]} /></span>
          <div style={{ minWidth: 0 }}>
            <div className="spread">
              <span className="feed-title">
                {a.link ? <Link to={`/cases/${a.link.kind}/${a.link.id}`}>{a.title}</Link> : a.title}
              </span>
              <time className="muted small" dateTime={a.at} style={{ whiteSpace: "nowrap" }}>{timeAgo(a.at)}</time>
            </div>
            {a.detail && <div className="muted small" style={{ overflowWrap: "anywhere" }}>{a.detail}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

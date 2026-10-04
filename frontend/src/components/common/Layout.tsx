import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../../lib/api/endpoints";
import { date } from "../../lib/format";
import { useApi, useRefresh } from "../../lib/useApi";
import { SyntheticBadge } from "./Badges";
import { DemoDialog } from "./DemoDialog";
import { Icon } from "./Icon";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/findings", label: "Findings" },
  { to: "/opportunities", label: "Opportunities" },
  { to: "/products", label: "Products" },
  { to: "/inventory", label: "Inventory" },
  { to: "/suppliers", label: "Suppliers" },
  { to: "/decisions", label: "Decisions", count: "pending" as const },
  { to: "/activity", label: "Activity" },
  { to: "/data", label: "Data & System" },
];

export function Layout() {
  const [demoOpen, setDemoOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const { refresh } = useRefresh();
  const location = useLocation();
  const overview = useApi(() => api.overview(), [location.pathname]);
  const pending = overview.data?.counts.pending_decisions ?? 0;

  const scan = async () => {
    setScanning(true);
    setScanError(null);
    try {
      await api.scan();
      refresh();
    } catch (e) {
      setScanError((e as Error).message);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="shell">
      <a href="#main" className="sr-only">Skip to content</a>
      <aside className="sidebar" data-open={menuOpen} aria-label="Main navigation">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">R</div>
          <div>
            <div className="brand-name">REACT Merchant</div>
            <div className="brand-sub">Know what matters, what it is worth, what to do</div>
          </div>
        </div>
        <nav className="nav" onClick={() => setMenuOpen(false)}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <span>{n.label}</span>
              {n.count === "pending" && pending > 0 && <span className="nav-count" aria-label={`${pending} waiting for approval`}>{pending}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="nav-foot">
          REACT watches your sales, stock and suppliers, explains each finding with your own records, prices it in rupees and recommends an action. You approve every action, and actions are simulated.
          <div style={{ marginTop: 8 }}><NavLink to="/setup">Set up a demo business</NavLink></div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-context">
            <button className="btn btn-sm menu-toggle" onClick={() => setMenuOpen((o) => !o)} aria-label="Open navigation" aria-expanded={menuOpen}>
              <span style={{ width: 16, display: "inline-flex" }}><Icon name="menu" /></span>
            </button>
            <strong>{overview.data?.company.name ?? "REACT Merchant"}</strong>
            {overview.data?.merchant?.city && <span className="muted small topbar-place">{overview.data.merchant.stores.map((s) => s.locality).join(", ")}, {overview.data.merchant.city}</span>}
            {overview.data?.as_of && <span className="muted small topbar-date">Business date {date(overview.data.as_of)}</span>}
            <SyntheticBadge />
          </div>
          <div className="topbar-actions">
            <button className="btn btn-sm" onClick={scan} disabled={scanning} title="Check the current sales, stock and supplier records for risks and opportunities" aria-label={scanning ? "Scanning the business" : "Scan business"}>
              <span style={{ width: 14, display: "inline-flex" }}><Icon name={scanning ? "clock" : "search"} /></span>
              <span className="btn-label">{scanning ? "Scanning…" : "Scan business"}</span>
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => setDemoOpen(true)} aria-label="Demo scenarios">
              <span style={{ width: 14, display: "inline-flex" }}><Icon name="play" /></span>
              <span className="btn-label">Demo scenarios</span>
            </button>
          </div>
        </header>
        {scanError && <div className="content" style={{ paddingBottom: 0 }}><div className="callout callout-warn" role="alert"><Icon name="warn" />Scan failed: {scanError}</div></div>}
        <main id="main" className="content">
          <Outlet />
        </main>
      </div>
      {demoOpen && <DemoDialog onClose={() => setDemoOpen(false)} />}
    </div>
  );
}

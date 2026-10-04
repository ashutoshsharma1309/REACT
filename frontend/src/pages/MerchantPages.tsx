import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api/endpoints";
import type { InventoryRow, MerchantProfile, ProductRow, StockStatus, SupplierRow } from "../lib/types";
import { money, number, pct } from "../lib/format";
import { useApi, useRefresh } from "../lib/useApi";
import { FindingChips } from "../components/common/FindingChips";
import { Icon } from "../components/common/Icon";
import { EmptyState, ErrorState, LoadingState } from "../components/common/States";

function Trend({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">–</span>;
  if (Math.abs(value) < 0.025) return <span className="muted">Steady</span>;
  return <span className={value >= 0 ? "trend-up" : "trend-down"}>{value >= 0 ? "▲" : "▼"} {pct(value, 0)}</span>;
}

// ---- products ------------------------------------------------------------------------------------------
type ProductSort = "revenue" | "trend" | "margin" | "flagged";

export function ProductsPage() {
  const { data, error, loading, reload } = useApi(() => api.products(), []);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState<ProductSort>("revenue");
  const categories = useMemo(() => Array.from(new Set((data ?? []).map((p) => p.category))).sort(), [data]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = (data ?? []).filter((p) => (!category || p.category === category) && (!needle || p.name.toLowerCase().includes(needle) || p.sku.toLowerCase().includes(needle)));
    const key: Record<ProductSort, (p: ProductRow) => number> = {
      revenue: (p) => -(p.revenue_28d ?? 0),
      trend: (p) => -(p.demand_change_pct ?? -1e9),
      margin: (p) => -(p.gross_margin_28d ?? 0),
      flagged: (p) => -p.findings.length * 1e9 - (p.revenue_28d ?? 0),
    };
    return [...list].sort((a, b) => key[sort](a) - key[sort](b));
  }, [data, q, category, sort]);

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Products</h1>
          <p>How each product is selling over the last 28 days, what it earns, whether demand is moving and how much stock is left.</p>
        </div>
        {data && <span className="muted small">{number(rows.length)} of {number(data.length)} products</span>}
      </div>
      <div className="toolbar" role="search">
        <input className="input" placeholder="Search by name or SKU" aria-label="Search products" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="select" aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as ProductSort)}>
          <option value="revenue">Highest revenue</option>
          <option value="margin">Highest gross margin</option>
          <option value="trend">Fastest growing</option>
          <option value="flagged">Has findings</option>
        </select>
      </div>
      {loading && !data && <LoadingState title="Loading products" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Product</th>
                <th scope="col">Category</th>
                <th scope="col" className="amt">Price</th>
                <th scope="col" className="amt">Margin at price</th>
                <th scope="col" className="amt">Sold (28 d)</th>
                <th scope="col" className="amt">Revenue (28 d)</th>
                <th scope="col" className="amt">Demand trend</th>
                <th scope="col" className="amt">Stock</th>
                <th scope="col">Findings</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 150).map((p) => (
                <tr key={p.id}>
                  <td><div>{p.name}</div><div className="muted small mono">{p.sku}{p.supplier ? ` · ${p.supplier}` : ""}</div></td>
                  <td className="small">{p.category}</td>
                  <td className="amt">{money(p.selling_price)}</td>
                  <td className="amt">{p.margin_rate_at_price !== null ? pct(p.margin_rate_at_price * 100, 0) : "–"}</td>
                  <td className="amt">{number(p.units_28d)}</td>
                  <td className="amt">{money(p.revenue_28d ?? 0)}</td>
                  <td className="amt"><Trend value={p.demand_change_pct} /></td>
                  <td className="amt">{number(p.stock_on_hand)}{p.merchant_days_of_cover !== null ? <div className="muted small">{number(p.merchant_days_of_cover, 0)} days</div> : null}</td>
                  <td><FindingChips findings={p.findings} /></td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={9} className="muted">No product matches.</td></tr>}
            </tbody>
          </table>
          {rows.length > 150 && <p className="muted small" style={{ padding: "10px 12px" }}>Showing the first 150. Narrow the search to see more.</p>}
        </div>
      )}
    </div>
  );
}

// ---- inventory -----------------------------------------------------------------------------------------
const STATUS_WORDS: Record<StockStatus, [string, string]> = {
  OUT_OF_STOCK: ["Out of stock", "badge-critical"],
  LOW: ["Low", "badge-high"],
  OVERSTOCKED: ["Overstocked", "badge-medium"],
  HEALTHY: ["Healthy", "badge-good"],
};

export function InventoryPage() {
  const { data, error, loading, reload } = useApi(() => api.inventory(), []);
  const [status, setStatus] = useState<"" | StockStatus>("");
  const rows = useMemo(() => (data ?? []).filter((r) => !status || r.status === status), [data, status]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    (data ?? []).forEach((r) => { if (r.status) c[r.status] = (c[r.status] ?? 0) + 1; });
    return c;
  }, [data]);
  const value = useMemo(() => (data ?? []).reduce((s, r) => s + (r.stock_value ?? 0), 0), [data]);

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Inventory</h1>
          <p>Stock on the shelf, what is on order, how long it lasts at the current sales rate, and what it is worth at cost.</p>
        </div>
        {data && <span className="muted small">Stock value at cost <strong className="money">{money(value)}</strong></span>}
      </div>
      <div className="row" role="group" aria-label="Filter stock">
        <button className={`btn btn-sm ${status === "" ? "btn-primary" : ""}`} aria-pressed={status === ""} onClick={() => setStatus("")}>All ({number(data?.length ?? 0)})</button>
        {(Object.keys(STATUS_WORDS) as StockStatus[]).map((k) => (
          <button key={k} className={`btn btn-sm ${status === k ? "btn-primary" : ""}`} aria-pressed={status === k} onClick={() => setStatus(k)}>{STATUS_WORDS[k][0]} ({number(counts[k] ?? 0)})</button>
        ))}
      </div>
      {loading && !data && <LoadingState title="Loading inventory" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Status</th>
                <th scope="col" className="amt">On hand</th>
                <th scope="col" className="amt">On order</th>
                <th scope="col" className="amt">Sells / day</th>
                <th scope="col" className="amt">Lasts</th>
                <th scope="col" className="amt">Value at cost</th>
                <th scope="col">Supplier</th>
                <th scope="col">Findings</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 150).map((r: InventoryRow) => (
                <tr key={r.id}>
                  <td><div>{r.item_name}</div><div className="muted small">{r.store}</div></td>
                  <td>{r.status && <span className={`badge ${STATUS_WORDS[r.status][1]}`}>{STATUS_WORDS[r.status][0]}</span>}</td>
                  <td className="amt">{number(r.quantity_available)}</td>
                  <td className="amt">{number(r.on_order ?? 0)}{(r.overdue_purchase_orders ?? 0) > 0 && <div className="small" style={{ color: "var(--critical)" }}>{r.overdue_purchase_orders} overdue</div>}</td>
                  <td className="amt">{number(r.avg_daily_consumption, 2)}</td>
                  <td className="amt">{r.days_of_cover >= 9999 ? "No recent sales" : `${number(r.days_of_cover, 1)} days`}</td>
                  <td className="amt">{money(r.stock_value ?? 0)}</td>
                  <td className="small">{r.supplier}</td>
                  <td><FindingChips findings={r.findings} /></td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={9} className="muted">No stock item matches.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- suppliers -----------------------------------------------------------------------------------------
export function SuppliersPage() {
  const { data, error, loading, reload } = useApi(() => api.suppliers(), []);
  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Suppliers</h1>
          <p>How reliably each supplier delivers: on-time rate, how long orders actually take against what they quote, open and overdue orders, and how much of your sales depends on them.</p>
        </div>
      </div>
      {loading && !data && <LoadingState title="Loading suppliers" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && !data.length && <div className="card"><EmptyState title="No suppliers yet" /></div>}
      {data && data.length > 0 && (
        <div className="card table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Supplier</th>
                <th scope="col" className="amt">Late, last 6</th>
                <th scope="col" className="amt">On time (45 d)</th>
                <th scope="col" className="amt">Lead time</th>
                <th scope="col" className="amt">Open orders</th>
                <th scope="col" className="amt">Products</th>
                <th scope="col" className="amt">Sales they supply (28 d)</th>
                <th scope="col">Findings</th>
              </tr>
            </thead>
            <tbody>
              {data.map((s: SupplierRow) => (
                <tr key={s.id}>
                  <td><div>{s.name}</div><div className="muted small">{s.component_type} · {s.location}</div></td>
                  <td className="amt" style={{ color: (s.late_of_last_6 ?? 0) >= 3 ? "var(--critical)" : undefined }}>{number(s.late_of_last_6)} of {number(s.deliveries_considered)}</td>
                  <td className="amt">{s.on_time_rate_recent !== null ? pct(s.on_time_rate_recent * 100, 0) : "–"}{s.on_time_rate_normal !== null && <div className="muted small">normally {pct(s.on_time_rate_normal * 100, 0)}</div>}</td>
                  <td className="amt">{s.recent_lead_days !== null ? `${number(s.recent_lead_days, 1)} d` : "–"}<div className="muted small">quoted {s.baseline_lead_time_days} d</div></td>
                  <td className="amt">{number(s.open_purchase_orders)}{(s.overdue_purchase_orders ?? 0) > 0 && <div className="small" style={{ color: "var(--critical)" }}>{s.overdue_purchase_orders} overdue</div>}</td>
                  <td className="amt">{number(s.products)}</td>
                  <td className="amt">{money(s.revenue_28d ?? 0)}</td>
                  <td><FindingChips findings={s.findings} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- onboarding ----------------------------------------------------------------------------------------
export function SetupPage() {
  const merchant = useApi(() => api.merchant(), []);
  const templates = useApi(() => api.merchantTemplates(), []);
  const [form, setForm] = useState<MerchantProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { refresh } = useRefresh();
  const navigate = useNavigate();
  const current = form ?? (merchant.data?.profile ? { ...merchant.data.profile } : null);
  const tpl = templates.data?.find((t) => t.key === (current?.business_type ?? "electronics")) ?? templates.data?.[0];
  const set = (patch: Partial<MerchantProfile>) => current && setForm({ ...current, ...patch });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await api.setupMerchant(current);
      refresh();
      navigate("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-lg">
      <div className="page-head">
        <div>
          <h1>Set up a demo business</h1>
          <p>Describe the shop and REACT simulates 120 days of its sales, stock, purchases, suppliers and customers. Everything it creates is synthetic; there is no account and no real data.</p>
        </div>
      </div>
      {(merchant.loading || templates.loading) && !current && <LoadingState title="Loading" />}
      {(merchant.error || templates.error) && <ErrorState error={(merchant.error ?? templates.error)!} onRetry={() => { merchant.reload(); templates.reload(); }} />}
      {current && tpl && (
        <form className="card card-pad stack" onSubmit={submit} aria-busy={busy}>
          <div className="form-grid">
            <div className="field"><label htmlFor="bn">Business name</label><input id="bn" className="input" required minLength={2} maxLength={80} value={current.business_name} onChange={(e) => set({ business_name: e.target.value })} /></div>
            <div className="field"><label htmlFor="bt">Business type</label>
              <select id="bt" className="select" value={current.business_type} onChange={(e) => set({ business_type: e.target.value })}>
                {templates.data!.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
            </div>
            <div className="field"><label htmlFor="city">City</label><input id="city" className="input" value={current.city} onChange={(e) => set({ city: e.target.value })} /></div>
            <div className="field"><label htmlFor="loc">Locality of the main store</label><input id="loc" className="input" value={current.locality} onChange={(e) => set({ locality: e.target.value })} /></div>
            <div className="field"><label htmlFor="st">State</label><input id="st" className="input" value={current.state} onChange={(e) => set({ state: e.target.value })} /></div>
            <div className="field"><label htmlFor="sc">Number of stores</label>
              <select id="sc" className="select" value={current.store_count} onChange={(e) => set({ store_count: Number(e.target.value) })}>
                {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          </div>
          <fieldset className="stack" style={{ border: "none", padding: 0, margin: 0 }}>
            <legend className="section-title">Product categories (at least 3)</legend>
            <div className="checks">
              {tpl.categories.map((c) => {
                const on = current.categories.length === 0 || current.categories.includes(c.code);
                const all = current.categories.length ? current.categories : tpl.categories.map((x) => x.code);
                return (
                  <label key={c.code} className="check">
                    <input type="checkbox" checked={on} onChange={() => set({ categories: on ? all.filter((x) => x !== c.code) : [...all, c.code] })} />
                    <span><strong>{c.name}</strong><span className="muted small" style={{ display: "block" }}>{c.description}</span></span>
                  </label>
                );
              })}
            </div>
          </fieldset>
          <div className="callout"><Icon name="info" /><div>Generating replaces the current demo business, including its findings, decisions and activity. It takes a few seconds.</div></div>
          {error && <p role="alert" className="small" style={{ color: "var(--critical)" }}>{error}</p>}
          <div className="row"><button className="btn btn-primary btn-lg" disabled={busy}>{busy ? "Generating…" : "Generate demo business"}</button></div>
        </form>
      )}
    </div>
  );
}

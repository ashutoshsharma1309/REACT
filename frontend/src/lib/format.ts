// Presentation formatting only. Values are always the backend's; nothing is rounded into a different claim.

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** ₹ amounts in Indian notation: ₹39.0 lakh, ₹1.27 crore, ₹84,000. */
export function money(value: number | null | undefined, currency = "INR"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "Not estimated";
  const sign = value < 0 ? "−" : "";
  const v = Math.abs(value);
  const symbol = currency === "INR" ? "₹" : `${currency} `;
  if (currency === "INR" && v >= 1e7) return `${sign}${symbol}${(v / 1e7).toFixed(2)} crore`;
  if (currency === "INR" && v >= 1e5) return `${sign}${symbol}${(v / 1e5).toFixed(1)} lakh`;
  return `${sign}${symbol}${inr.format(v)}`;
}

/** Exact rupee amount for calculation details. */
export function moneyExact(value: number | null | undefined, currency = "INR"): string {
  if (value === null || value === undefined) return "n/a";
  return `${currency === "INR" ? "₹" : currency + " "}${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)}`;
}

export function number(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "n/a";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(value);
}

export function pct(value: number | null | undefined, digits = 0, signed = false): string {
  if (value === null || value === undefined) return "n/a";
  const s = `${Math.abs(value).toFixed(digits)}%`;
  if (!signed) return s;
  return value > 0 ? `+${s}` : value < 0 ? `−${s}` : s;
}

/** Backend timestamps are naive UTC ISO strings. */
export function parseUtc(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  return parseUtc(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  return parseUtc(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const s = Math.max(0, Math.round((now.getTime() - parseUtc(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return date(iso);
}

export function humanize(code: string | null | undefined): string {
  if (!code) return "";
  return code
    .toLowerCase()
    .split("_")
    .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

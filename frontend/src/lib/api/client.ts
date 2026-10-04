// Thin fetch wrapper. The backend is authoritative: errors are surfaced as-is, never replaced
// with plausible-looking values.

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : String(d))).join("; ");
  }
  return "Unexpected response from the REACT server";
}

/** Like request(), but also returns the body for the listed non-2xx statuses (e.g. /ready answers 503 with details). */
export async function getAllowing<T>(path: string, allowed: number[]): Promise<{ status: number; body: T }> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`);
  } catch {
    throw new ApiError(0, "Cannot reach the REACT server. Check that the backend is running.");
  }
  const text = await res.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = text;
  }
  if (!res.ok && !allowed.includes(res.status)) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? describe((payload as { detail: unknown }).detail) : `Request failed (${res.status})`;
    throw new ApiError(res.status, detail);
  }
  return { status: res.status, body: payload as T };
}

export async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the REACT server. Check that the backend is running.");
  }
  const text = await res.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!res.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? describe((payload as { detail: unknown }).detail) : `Request failed (${res.status})`;
    throw new ApiError(res.status, detail);
  }
  return payload as T;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});

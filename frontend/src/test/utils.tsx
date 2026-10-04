import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "../App";

type Handler = unknown | ((body: unknown) => unknown);
export interface MockCall { method: string; path: string; body: unknown }

/** Stub fetch with a route table keyed by "METHOD /path" (query-string optional). Unknown routes return 404. */
export function mockApi(routes: Record<string, Handler>, opts: { status?: Record<string, number> } = {}) {
  const calls: MockCall[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: path + url.search, body });
    const key = [`${method} ${path}${url.search}`, `${method} ${path}`].find((k) => k in routes);
    const status = key ? opts.status?.[key] ?? 200 : 404;
    const handler = key ? routes[key] : { detail: `no mock for ${method} ${path}` };
    const payload = typeof handler === "function" ? (handler as (b: unknown) => unknown)(body) : handler;
    return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

export function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

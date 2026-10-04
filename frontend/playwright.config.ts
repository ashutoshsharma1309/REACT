import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run the real UI against a real backend on an isolated database (react_e2e).
const API_PORT = Number(process.env.E2E_API_PORT ?? 8010);
const UI_PORT = Number(process.env.E2E_UI_PORT ?? 5181);

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${UI_PORT}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /mobile\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [
    { command: "../scripts/e2e_backend.sh", url: `http://127.0.0.1:${API_PORT}/health`, reuseExistingServer: false, timeout: 120_000, env: { E2E_API_PORT: String(API_PORT) } },
    { command: `npx vite --host 127.0.0.1 --port ${UI_PORT} --strictPort`, url: `http://127.0.0.1:${UI_PORT}`, reuseExistingServer: false, timeout: 60_000, env: { REACT_API_TARGET: `http://127.0.0.1:${API_PORT}` } },
  ],
});

import { expect, test } from "@playwright/test";
import { approve, investigate, loadScenario } from "./helpers";

// The five REACT Merchant scenarios end to end against the real backend (isolated react_e2e database):
// DETECT → INVESTIGATE → QUANTIFY (₹) → RECOMMEND → HUMAN APPROVAL → SIMULATED ACTION → AUDIT.

test("stockout crisis: from the dashboard to a simulated reorder and back", async ({ page }) => {
  // 1. the scenario makes the business status immediately clear
  await loadScenario(page, "Stockout crisis", "dashboard");
  await expect(page.getByText(/Business status: Critical/i)).toBeVisible();
  const attention = page.locator("section").filter({ has: page.getByRole("heading", { name: /needs your attention/i }) });
  await expect(attention.getByText("CRITICAL").first()).toBeVisible();
  await expect(attention.getByText("Stockout risk").first()).toBeVisible();
  // 2. open the critical stockout risk
  await attention.locator("a.finding").filter({ hasText: "CRITICAL" }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/out of stock|may run out/);
  // 3. investigate: what happened, why, evidence, cost
  await investigate(page);
  await expect(page.getByText("What happened", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /why does react think this/i }).click();
  const why = page.getByRole("dialog");
  await expect(why.getByText(/Evidence Support Score/)).toBeVisible();
  await why.getByRole("button", { name: /see the source records/i }).click();
  await expect(page.getByRole("heading", { name: /How REACT reached its conclusion/i })).toBeVisible();
  await expect(page.getByRole("table").getByText("Supports").first()).toBeVisible();
  await page.getByRole("tab", { name: "Overview" }).click();
  await expect(page.getByText("What it could cost", { exact: true })).toBeVisible();
  await expect(page.getByText(/^₹[\d,.]+( lakh| crore)?$/).first()).toBeVisible();
  // 4. the recommendation is a reorder with a quantity REACT worked out deterministically
  await expect(page.getByText(/REACT recommends/i)).toBeVisible();
  await expect(page.getByText(/^Reorder now: /)).toBeVisible();
  await expect(page.getByText(/What REACT worked out/i)).toBeVisible();
  // 5. approve → simulated purchase order, nothing real
  await approve(page);
  await expect(page.getByText(/no purchase order, price change, message or payment was sent/i)).toBeVisible();
  // 6. back on the dashboard the issue is handled
  await page.getByRole("link", { name: "Dashboard" }).click();
  const handled = page.locator("section").filter({ has: page.getByRole("heading", { name: /^Handled$/ }) });
  await expect(handled.getByText(/Done \(simulated\)/i).first()).toBeVisible();
  await page.getByRole("link", { name: "Activity" }).click();
  await expect(page.getByText("Simulated action completed").first()).toBeVisible();
});

test("demand opportunity: local demand, potential value and a stock-up plan", async ({ page }) => {
  await loadScenario(page, "Demand opportunity");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/selling .* faster/);
  await investigate(page);
  await expect(page.getByText("Why it is happening")).toBeVisible();
  // at least three plain-language reasons from stored evidence
  expect(await page.locator(".checklist li.check-yes").count()).toBeGreaterThanOrEqual(3);
  await expect(page.getByText("What it could be worth", { exact: true })).toBeVisible();
  await expect(page.getByText(/^Stock up: /)).toBeVisible();
  await page.getByRole("tab", { name: "Evidence" }).click();
  await expect(page.getByRole("table").getByText("Supports").first()).toBeVisible();
  await page.getByRole("tab", { name: "Overview" }).click();
  await approve(page);
});

test("supplier delay: late deliveries traced to the supplier and a simulated review", async ({ page }) => {
  await loadScenario(page, "Supplier delay");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/overdue|late/);
  await investigate(page);
  await expect(page.getByText(/^Review the supplier: /)).toBeVisible();
  await approve(page);
  await page.getByRole("link", { name: "Suppliers" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Suppliers" })).toBeVisible();
  await expect(page.getByRole("table").getByText(/overdue/).first()).toBeVisible();
});

test("margin leakage: a price review that changes no price", async ({ page }) => {
  await loadScenario(page, "Margin leakage");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/earns less on every sale/);
  await expect(page.getByText(/^Margin leakage ·/).first()).toBeVisible();
  await investigate(page);
  await expect(page.getByText(/^Review the price: /)).toBeVisible();
  const before = await (await page.request.get("/api/products")).json();
  await approve(page);
  // the action is simulated: the shop's prices are exactly as they were
  const after = await (await page.request.get("/api/products")).json();
  const prices = (rows: { sku: string; selling_price: number }[]) => Object.fromEntries(rows.map((r) => [r.sku, r.selling_price]));
  expect(prices(after)).toEqual(prices(before));
});

test("excess inventory: cash locked in slow stock and a simulated promotion", async ({ page }) => {
  await loadScenario(page, "Excess inventory");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/stock|sold/i);
  await investigate(page);
  await expect(page.getByText(/^Run a promotion: /)).toBeVisible();
  const before = await (await page.request.get("/api/inventory")).json();
  await approve(page);
  const after = await (await page.request.get("/api/inventory")).json();
  const stock = (rows: { id: string; quantity_available: number }[]) => Object.fromEntries(rows.map((r) => [r.id, r.quantity_available]));
  expect(stock(after)).toEqual(stock(before));
});

test("rejecting a recommendation records the reason and takes no action", async ({ page }) => {
  await loadScenario(page, "Supplier delay");
  await investigate(page);
  await page.getByLabel("Your name").fill("Store owner");
  await page.getByRole("button", { name: /^reject$/i }).click();
  await page.getByLabel(/why are you rejecting/i).fill("Already spoke to the supplier this morning");
  await page.getByRole("button", { name: /confirm rejection/i }).click();
  await expect(page.getByText(/No action was taken/i)).toBeVisible();
  await expect(page.getByText("Action completed")).toHaveCount(0);
  await page.getByRole("link", { name: "Decisions" }).click();
  await expect(page.getByRole("heading", { name: /^Rejected/ })).toBeVisible();
});

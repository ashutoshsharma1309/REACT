import { expect, test, type Page } from "@playwright/test";
import { loadScenario } from "./helpers";

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

test("mobile: dashboard, navigation and a case remain usable without sideways scrolling", async ({ page }) => {
  await loadScenario(page, "Excess inventory", "dashboard");
  await expect(page.getByText(/Business status:/i)).toBeVisible();
  expect(await overflow(page)).toBeLessThanOrEqual(1);
  for (const [link, heading] of [["Findings", "Findings"], ["Products", "Products"], ["Inventory", "Inventory"], ["Suppliers", "Suppliers"]]) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(1);
  }
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Findings", exact: true }).click();
  await page.locator("a.finding").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await overflow(page)).toBeLessThanOrEqual(1);
});

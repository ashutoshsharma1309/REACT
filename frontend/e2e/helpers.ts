import { expect, type Page } from "@playwright/test";

/** Load a deterministic merchant scenario through the Demo scenarios dialog. */
export async function loadScenario(page: Page, label: string, open: "finding" | "dashboard" = "finding") {
  await page.goto("/");
  await page.getByRole("button", { name: "Demo scenarios" }).click();
  await page.getByRole("button", { name: new RegExp(`^${label}`, "i") }).click();
  await expect(page.getByText(`${label} loaded.`)).toBeVisible({ timeout: 90_000 });
  if (open === "finding") await page.getByRole("button", { name: /open the main finding/i }).click();
  else await page.getByRole("button", { name: /back to dashboard/i }).click();
}

/** Run the REACT workflow on the open finding and wait for the recommendation. */
export async function investigate(page: Page) {
  await page.getByRole("button", { name: /investigate with react/i }).click();
  await expect(page.getByRole("heading", { name: /what should i do/i })).toBeVisible({ timeout: 90_000 });
}

/** Approve the recommendation as the store owner and check the simulated result. */
export async function approve(page: Page, reviewer = "Store owner") {
  await page.getByLabel("Your name").fill(reviewer);
  await page.getByRole("button", { name: /^approve$/i }).click();
  await expect(page.getByText("Action completed")).toBeVisible();
  await expect(page.getByText(/This was a/)).toContainText("simulated action");
  await expect(page.getByText(/^SIM-[A-Z]+-/).first()).toBeVisible();
}

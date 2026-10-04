import { expect, test, type Page } from "@playwright/test";
import { investigate, loadScenario } from "./helpers";

// Safe AI-failure demo against the real backend. The drill only makes the language model "unreachable";
// no AWS service is called. The test always switches the drill back off.

async function setDrill(page: Page, label: RegExp) {
  await page.goto("/data");
  await page.getByRole("radio", { name: label }).check();
  await expect(page.getByRole("radio", { name: label })).toBeChecked();
}

test("AI failure: no substitute means no answer; rule-based fallback is labelled; approval stores an evidence pack", async ({ page }) => {
  try {
    // 1. AI down and no substitute allowed: REACT says so and invents nothing
    await setDrill(page, /AI down, no substitute/i);
    await loadScenario(page, "Stockout crisis");
    await page.getByRole("button", { name: /investigate with react/i }).click();
    await expect(page.getByRole("heading", { name: /AI investigation temporarily unavailable/i })).toBeVisible();
    await expect(page.getByText(/did not produce a conclusion or a recommendation/i)).toBeVisible();
    await expect(page.getByText(/REACT recommends/i)).toHaveCount(0);

    // 2. AI down, rule-based analysis allowed: the same evidence-based answer, clearly labelled
    await setDrill(page, /AI down, use rule-based analysis/i);
    await loadScenario(page, "Stockout crisis");
    await investigate(page);
    await expect(page.getByText(/REACT recommends/i)).toBeVisible();
    await expect(page.getByText(/AI reasoning was unavailable for part of this investigation/i)).toBeVisible();

    // 3. approval writes a verifiable evidence pack (local storage here, S3 on AWS)
    await page.getByLabel("Your name").fill("Store owner");
    await page.getByRole("button", { name: /^approve$/i }).click();
    await expect(page.getByText("Action completed")).toBeVisible();
    const link = page.getByRole("link", { name: /download evidence pack/i });
    await expect(link).toBeVisible();
    const res = await page.request.get((await link.getAttribute("href"))!);
    expect(res.status()).toBe(200);
    const pack = await res.json();
    expect(pack.provenance.data).toBe("SYNTHETIC");
    expect(pack.decision.fallback_used).toBe(true);
    expect(res.headers()["x-content-sha256"]).toMatch(/^[0-9a-f]{64}$/);
  } finally {
    await setDrill(page, /^normal/i);
  }
});

import { test, expect } from "@playwright/test";

/**
 * Verifies the PRODUCTION build (the `dist/` folder Render serves) against the
 * live backend, including deep links into the SPA.
 */
test("production build works against the live backend", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400 && !r.url().includes("fonts.g")) {
      errors.push(`[http ${r.status()}] ${r.url()}`);
    }
  });

  // Start from the deterministic demo state so the review queue has work.
  const reset = await page.request.post("http://127.0.0.1:8000/demo/reset");
  expect(reset.ok()).toBeTruthy();

  await page.goto("http://127.0.0.1:4173/");
  await expect(page.getByTestId("data-mode")).toHaveText("Live", { timeout: 15_000 });
  await expect(page.getByTestId("backend-banner")).toHaveCount(0);

  // Deep link into a client-side route.
  await page.goto("http://127.0.0.1:4173/review");
  await expect(page.getByText("Review queue").first()).toBeVisible();
  await expect(page.getByTestId("queue-item-Revenue")).toBeVisible({ timeout: 20_000 });

  // Deep link into a filing detail.
  await page.goto("http://127.0.0.1:4173/filings/audit-seed-paused");
  await expect(page.getByTestId("thread-id")).toBeVisible({ timeout: 20_000 });

  // The audit log renders its rows and the chain verifies.
  await page.goto("http://127.0.0.1:4173/audit");
  await page.getByTestId("verify-chain-button").click();
  await expect(page.getByTestId("verify-result")).toContainText("intact", { timeout: 20_000 });

  expect(errors).toEqual([]);
});

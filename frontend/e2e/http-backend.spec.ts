import { test, expect } from "@playwright/test";

// The sandbox has no outbound access to the Google Fonts CDN, which is
// unrelated to the application under test.
const isExternalFont = (url: string) =>
  url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com");

test("integrated backend: live UI lifecycle against the FastAPI contract", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("response", (r) => {
    if (r.status() >= 400 && !isExternalFont(r.url())) {
      consoleErrors.push(`[http ${r.status()}] ${r.request().method()} ${r.url()}`);
    }
  });
  page.on("console", (msg) => {
    const text = msg.text();
    if (msg.type() === "error" && !isExternalFont(text)) consoleErrors.push(text);
  });
  page.on("pageerror", (err) => consoleErrors.push(String(err)));
  page.on("requestfailed", (r) => {
    const url = r.url();
    if (isExternalFont(url)) return;
    const reason = r.failure()?.errorText ?? "";
    // ERR_ABORTED is a request the browser cancelled because the page
    // navigated away mid-flight, not a backend failure.
    if (reason.includes("ERR_ABORTED")) return;
    consoleErrors.push(`[reqfail] ${r.method()} ${url} :: ${reason}`);
  });

  await page.goto("/");
  await expect(page.getByTestId("data-mode")).toHaveText("Live", { timeout: 20_000 });
  await expect(page.getByTestId("demo-pill")).toHaveText("Live");

  await page.goto("/new");
  await expect(page.getByTestId("company-card-AAPL")).toBeVisible();
  await expect(page.getByTestId("company-card-MSFT")).toBeVisible();

  // Reset the deterministic demo state so the test is repeatable: the review
  // queue always starts from a freshly seeded paused thread.
  const reset = await page.request.post("http://127.0.0.1:8000/demo/reset");
  expect(reset.ok()).toBeTruthy();

  await page.goto("/review");
  const queueItem = page.getByTestId("queue-item-Revenue");
  await expect(queueItem).toBeVisible({ timeout: 20_000 });

  await queueItem.click();
  await page.getByRole("button", { name: "Approve", exact: true }).click();

  // The submit action requires a decision for every pending flagged item, so
  // approve the whole pending set (single-item case included).
  const bulkButton = page.getByRole("button", { name: /Bulk approve/i });
  await expect(bulkButton).toBeEnabled({ timeout: 10_000 });
  await bulkButton.click();
  await page.getByRole("button", { name: /^Approve \d+$/ }).click();

  await expect(page.getByText(/Submitted \d+ decision/i)).toBeVisible({ timeout: 90_000 });

  await page.goto("/audit");
  await page.getByRole("button", { name: /Verify chain/i }).click();
  await expect(page.getByTestId("verify-result")).toContainText("intact", { timeout: 20_000 });

  expect(consoleErrors).toEqual([]);
});

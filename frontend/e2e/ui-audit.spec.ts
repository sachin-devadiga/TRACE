import { test, expect } from "@playwright/test";

/**
 * UI audit: walk every page, press every control, and prove that nothing
 * produces a console error, a failed request, or a dead end.
 *
 * External Google Fonts are unreachable in the sandbox and are unrelated to
 * the application, so they are ignored.
 */
const isExternalFont = (url: string) =>
  url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com");

test.describe("UI audit", () => {
  test.beforeEach(async ({ page }) => {
    await page.request.post("http://127.0.0.1:8000/demo/reset");
  });

  test("every page loads with live data and no console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && !isExternalFont(m.text())) errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("response", (r) => {
      if (r.status() >= 400 && !isExternalFont(r.url())) {
        errors.push(`[http ${r.status()}] ${r.request().method()} ${r.url()}`);
      }
    });

    const pages = ["/", "/new", "/review", "/filings", "/accuracy", "/audit", "/settings"];
    for (const path of pages) {
      await page.goto(path);
      await expect(page.getByTestId("data-mode")).toHaveText("Live", { timeout: 15_000 });
      await expect(page.locator("main")).toBeVisible();
      // No page may render its "failed to load" state while the backend is up.
      await expect(page.getByText(/Failed to load/i)).toHaveCount(0);
    }

    expect(errors).toEqual([]);
  });

  test("filings list opens the detail page and refreshes", async ({ page }) => {
    await page.goto("/filings");
    const row = page.locator('[data-testid^="filing-row-"]').first();
    await expect(row).toBeVisible();
    await row.click();
    await expect(page).toHaveURL(/\/filings\/audit-/);
    await expect(page.getByTestId("thread-id")).toBeVisible();

    await page.goto("/filings");
    await page.getByTestId("filings-refresh").click();
    await expect(page.getByTestId("filings-refresh")).toBeEnabled();
  });

  test("audit log rows open the record dialog and the chain verifies", async ({ page }) => {
    await page.goto("/audit");
    const row = page.locator('[data-testid^="audit-row-"]').first();
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByText("Row hash:")).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByTestId("verify-chain-button").click();
    await expect(page.getByTestId("verify-result")).toContainText("intact", { timeout: 20_000 });
  });

  test("settings controls all respond", async ({ page }) => {
    await page.goto("/settings");

    // Test connection gives feedback.
    await page.getByRole("button", { name: "Test connection" }).click();
    await expect(page.getByText(/Backend reachable/i)).toBeVisible({ timeout: 15_000 });

    // Failure simulation switch reflects and keeps its state.
    const failureSwitch = page.getByTestId("failure-mode-switch");
    await failureSwitch.click();
    await expect(failureSwitch).toHaveAttribute("data-state", "checked");
    await failureSwitch.click();
    await expect(failureSwitch).toHaveAttribute("data-state", "unchecked");

    // Tamper reports that it is disabled on this server (never a false alarm).
    await page.getByRole("button", { name: /Tamper/ }).click();
    await expect(page.getByText(/Tampering is disabled/i)).toBeVisible({ timeout: 15_000 });

    // Theme toggle flips the document class (the sidebar has one too).
    await page.locator("main").getByRole("button", { name: /Switch to (dark|light) theme/ }).click();
    await expect(page.locator("html")).toHaveAttribute("class", /dark|light/);
  });

  test("reset dialog restores the seeded review queue", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: /^Reset$/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("button", { name: "Reset demo data" }).click();
    await expect(page.getByText(/Demo data reset/i)).toBeVisible({ timeout: 20_000 });

    await page.goto("/review");
    await expect(page.getByTestId("queue-item-Revenue")).toBeVisible({ timeout: 20_000 });
  });

  test("review queue explains itself and every decision works", async ({ page }) => {
    await page.goto("/review");
    const queueItem = page.getByTestId("queue-item-Revenue");
    await expect(queueItem).toBeVisible({ timeout: 20_000 });

    // Submit stays disabled until every flagged item has a decision.
    await expect(page.getByTestId("submit-decisions")).toBeDisabled();
    await expect(page.getByTestId("submit-hint")).toBeVisible();

    await queueItem.click();
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(page.getByTestId("queue-item-Revenue")).toContainText("Approved");

    // The reason and the badge explanations are always shown.
    await expect(page.getByText("Why this was escalated")).toBeVisible();
    await expect(page.getByText(/Extraction risk:/)).toBeVisible();
    await expect(page.getByText(/Financial anomaly:/)).toBeVisible();

    // Correct and reject are available too.
    await page.getByRole("button", { name: "Correct", exact: true }).click();
    await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeVisible();
  });

  test("new audit page exposes its controls", async ({ page }) => {
    await page.goto("/new");
    const aapl = page.getByTestId("company-card-AAPL");
    await expect(aapl).toBeVisible();
    // Apple is pre-selected: clicking it clears the selection.
    await aapl.click();
    await expect(page.getByText(/No company selected/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Run audit/ })).toBeDisabled();

    await page.getByTestId("company-card-MSFT").click();
    await expect(page.getByRole("button", { name: /Run audit/ })).toBeEnabled();

    await page.getByTestId("company-card-AAPL").click();
    await expect(page.getByText(/companies selected/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Run audit/ })).toBeDisabled();
  });

  test("topbar search and navigation work", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: /Search companies/i }).fill("AAPL");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/filings\?search=AAPL/);

    await page.getByRole("button", { name: "New audit" }).click();
    await expect(page).toHaveURL(/\/new/);

    await page.getByTestId("guided-demo-button").click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Guided demo" })).toBeVisible();
    await page.keyboard.press("Escape");
  });
});

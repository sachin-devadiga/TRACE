import { describe, expect, it } from "vitest";
import secSnapshot from "./sec-snapshot.json";

describe("sec-snapshot.json", () => {
  const companies = Object.values((secSnapshot as any).companies || {});
  
  it("has all companies with required metrics", () => {
    const required = ['Revenue', 'Net Income', 'Total Assets', 'Total Liabilities', "Shareholders' Equity", 'Operating Cash Flow', 'Cash and Cash Equivalents', 'R&D Expense'];
    for (const c of companies as any[]) {
      for (const m of required) {
        expect(c.metrics[m], `${c.ticker} missing ${m}`).toBeDefined();
        expect(c.metrics[m].value_usd, `${c.ticker} ${m} is null`).not.toBeNull();
      }
    }
  });
  
  it("validates balance sheet equation within 1%", () => {
    for (const c of companies as any[]) {
      const assets = c.metrics['Total Assets']?.value_usd;
      const liabilities = c.metrics['Total Liabilities']?.value_usd;
      const equity = c.metrics["Shareholders' Equity"]?.value_usd;
      if (assets == null || liabilities == null || equity == null) continue;
      const sum = liabilities + equity;
      const diff = Math.abs(assets - sum);
      const pct = assets === 0 ? 1 : diff / Math.abs(assets);
      if (c.metrics['Total Liabilities']?.derived) {
        // skip with note
        continue;
      }
      expect(pct <= 0.01).toBe(true);
    }
  });
});

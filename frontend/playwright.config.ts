import { defineConfig } from "@playwright/test";

/**
 * Two suites run against the same UI:
 *
 * - `live`  : the real FastAPI backend on port 8000 (VITE_API_MODE=http).
 * - `mock`  : the in-browser demo server on port 5174 (VITE_API_MODE=mock).
 *
 * The live project is the one that proves the integrated product works.
 */
const livePort = 5173;
const mockPort = 5174;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  retries: 0,
  fullyParallel: false,
  use: {
    viewport: { width: 1440, height: 900 },
    channel: "chrome",
  },
  projects: [
    {
      name: "live",
      testMatch: /(http-backend|ui-audit|production-build)\.spec\.ts/,
      use: { baseURL: `http://127.0.0.1:${livePort}` },
    },
    {
      name: "mock",
      testMatch: /smoke\.spec\.ts/,
      use: { baseURL: `http://127.0.0.1:${mockPort}` },
    },
  ],
  webServer: [
    {
      command: "npm run dev",
      url: `http://127.0.0.1:${livePort}`,
      env: { VITE_API_MODE: "http", VITE_API_BASE: "http://127.0.0.1:8000" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npx vite --port ${mockPort} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${mockPort}`,
      env: { VITE_API_MODE: "mock" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});

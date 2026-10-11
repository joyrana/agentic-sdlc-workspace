import { defineConfig, devices } from "@playwright/test";

/**
 * Browser E2E against the production build (`vite preview`), which proxies /api to a running API.
 * The API, worker and a seeded development database must already be up (see the CI workflow and
 * docs/testing/e2e.md). PW_CHROMIUM_PATH points at a preinstalled Chromium when downloads are
 * not possible.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 6 * 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  webServer: {
    command: "npm run preview -- --strictPort",
    url: "http://localhost:4173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});

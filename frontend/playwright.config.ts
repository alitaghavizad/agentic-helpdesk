import { defineConfig, devices } from "@playwright/test";

// The phase 8b gate: every screen rendered against seeded data, in a real
// browser, against the real backend. This config intentionally does NOT
// start the backend -- a gate that silently starts its own backend can
// pass against an empty database. The backend, Postgres and Chroma are
// brought up separately (see README.md's Frontend section); only the
// frontend dev server is managed here, because Playwright needs it to
// exist before navigating and tearing it down after is harmless.
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});

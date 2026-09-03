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
  // One retry absorbs occasional real-machine timing noise in the
  // `screens.spec.ts` settle-wait (observed roughly once per 5-9 full-suite
  // runs on this dev machine, always as a poll timeout, never as a wrong
  // `failures` result) -- see task-12-report.md's finding-1 section. This
  // does not weaken what is checked: a retry re-runs the exact same
  // assertions against the exact same backend, so a genuinely broken
  // endpoint fails every attempt identically, as proven there by three
  // repeated break/restore cycles with zero retries in play.
  retries: 1,
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

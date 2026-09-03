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
  // `retries: 1` was added, then removed, in review round 2/3 -- it was
  // covering for a specific bug in `screens.spec.ts`'s settle-wait (a
  // leftover request from the page `signInAsAdmin` left the browser on,
  // cancelled by `page.goto`'s navigation, incrementing `settled` without
  // a matching `started`), not a genuine flakiness risk in the gate
  // itself. Root-caused and fixed by resetting the settle counters right
  // after navigation commits (see `screens.spec.ts` and
  // task-12-report.md's review-round-3 section) -- `--repeat-each=3` was
  // run four times after the fix (192 total instances) with zero
  // failures, versus 2 failures in 48 instances before it. Retries are
  // back to 0 because the fix removes the reason for them, not because
  // the risk was judged acceptable to paper over.
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

import { test, expect } from "@playwright/test";
import { signInAsAdmin, startConversation } from "./fixtures";

// The parent spec's phase 8 gate: every one of the twelve routes renders
// its own content against seeded data. Each test asserts a screen-specific
// element AND that the page made zero failed API calls (status >= 400)
// during its load -- a screen that renders its empty state because every
// call 500'd must fail this gate, not pass it.
const SCREENS = [
  ["/admin", /runs today/i],
  ["/admin/conversations", /participant/i],
  ["/admin/traces", /duration/i],
  // Approvals and lessons are populated organically by live agent runs (a
  // privileged action needing sign-off; a reflection recorded after a run)
  // -- neither is part of `backend/app/db/seed.py`'s static seed, which
  // creates only user accounts. A freshly seeded database therefore
  // legitimately renders each screen's empty state, same as
  // "/admin/tickets" below; both markers accept that honestly rather than
  // asserting a shape the real, live seed never produces.
  ["/admin/approvals", /risk|no approvals/i],
  ["/admin/tickets", /generate dossier|no tickets/i],
  ["/admin/users", /clearance/i],
  ["/admin/lessons", /confidence|no lessons/i],
  ["/admin/audit", /actor/i],
  ["/admin/costs", /cache hit rate/i],
  ["/chat", /send/i],
  ["/tickets", /ticket/i],
] as const;

for (const [path, marker] of SCREENS) {
  test(`${path} renders against seeded data`, async ({ page }) => {
    // Signed in BEFORE the failure listener attaches: the /login page's own
    // boot effect always tries the httpOnly refresh cookie to see whether a
    // session already exists, and on a brand new browser context that
    // legitimately 401s (there is no cookie yet). That is normal session
    // bootstrapping, not a defect in the screen under test -- so only calls
    // made while loading THIS screen count toward the zero-failures bar.
    await signInAsAdmin(page);

    const failures: string[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/") && r.status() >= 400) failures.push(`${r.status()} ${r.url()}`);
    });
    await page.goto(path);
    // Chat's composer only renders once a conversation is selected (see
    // fixtures.ts's `startConversation`) -- whether admin already has one
    // depends on what earlier tests/runs did against this seeded database,
    // so always starting a fresh one keeps this deterministic.
    if (path === "/chat") await startConversation(page);
    await expect(page.getByText(marker).first()).toBeVisible();
    expect(failures).toEqual([]);
  });
}

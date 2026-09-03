import { test, expect } from "@playwright/test";
import { signInAsAdmin, startConversation } from "./fixtures";

// The parent spec's phase 8 gate: every one of the twelve routes renders
// its own content against seeded data. Each test asserts a screen-specific
// element AND that the page made zero failed API calls (status >= 400)
// during its load -- a screen that renders its empty state because every
// call 500'd must fail this gate, not pass it.
//
// Every marker below is checked to be DATA-DEPENDENT, not just present:
// each one is either a table column header that only renders inside that
// screen's success branch (StateBlock covers loading/error/empty instead),
// or the screen's own legitimate empty-state copy. A marker that could
// equally be a static label rendered regardless of the fetch's outcome --
// "/admin/audit" originally used `/actor/i`, which also matches the
// always-rendered "Actor ID" filter label, and plain "/tickets" originally
// used `/ticket/i`, which also matches the always-rendered "Tickets" <h1>
// -- would pass even when the screen's data call 500s, defeating the
// point of this file. Both were caught and fixed; see task-12-report.md.
const SCREENS = [
  ["/admin", /runs today/i],
  ["/admin/conversations", /participant/i],
  ["/admin/traces", /duration/i],
  // Approvals, lessons, tickets and audit are populated organically by
  // live agent runs and real admin actions (a privileged action needing
  // sign-off; a reflection recorded after a run; a ticket the agent
  // opened; a mutation this gate never performs) -- none of them are part
  // of `backend/app/db/seed.py`'s static seed, which creates only user
  // accounts. A freshly seeded, read-only-so-far database therefore
  // legitimately renders each screen's empty state; every marker below
  // accepts that honestly (matching a column header the real data would
  // add, OR the screen's own empty-state copy) rather than asserting a
  // shape the live seed never produces.
  ["/admin/approvals", /risk|no approvals/i],
  ["/admin/tickets", /generate dossier|no tickets/i],
  ["/admin/users", /clearance/i],
  ["/admin/lessons", /confidence|no lessons/i],
  // "Actor" alone would also match the always-rendered "Actor ID" filter
  // label regardless of whether the fetch succeeded -- "IP address" is a
  // column header found nowhere else on the screen, so it only appears
  // once the table itself renders.
  ["/admin/audit", /ip address|no matching entries/i],
  ["/admin/costs", /cache hit rate/i],
  ["/chat", /send/i],
  // Bare "ticket" would also match the always-rendered "Tickets" <h1> --
  // "Priority" is a column header with no same-named filter on this
  // screen (unlike "Status", which is both a filter label and a column
  // header), so it only appears once the table itself renders.
  ["/tickets", /priority|no tickets to show/i],
] as const;

/**
 * True for a real backend REST or SSE call, and false for everything else
 * this page loads -- most importantly Vite's own dev-server module
 * requests (`http://localhost:5173/src/api/client.ts`, `.../src/api/
 * endpoints/admin.ts`, ...), which also contain the substring "/api/"
 * purely because this project's frontend source happens to live under
 * `src/api/`. An earlier version of this file matched on that substring
 * appearing anywhere in the URL, which counted those module requests as
 * tracked calls too; Vite occasionally serves one of them slower than the
 * backend responds to the screen's real data call (a cold transform, a
 * 304 revalidation), which intermittently pushed the settle-wait below
 * past its timeout for reasons having nothing to do with the backend -- a
 * real, reproduced flake (seen on "/admin" and "/admin/traces", the only
 * two screens with a live SSE connection open alongside their REST call,
 * though the module requests responsible load on every screen). Requiring
 * the frontend dev server's own origin to be excluded, rather than just
 * requiring "/api/" to appear somewhere in the URL, is what makes this
 * deterministic. Used directly by the `failures` listener below, which
 * should catch a broken stream's status code if one ever arrives quickly,
 * even though `isTrackedApiCall` (below) does not wait on one to settle.
 */
function isBackendApiUrl(url: string): boolean {
  return new URL(url).host !== "localhost:5173" && new URL(url).pathname.startsWith("/api/");
}

/**
 * `isBackendApiUrl`, minus `/stream` endpoints -- the run-activity feed
 * Overview/Traces open, and the notification feed every signed-in screen's
 * `NotificationBell` opens. Those are excluded here, separately, because
 * they are meant to stay open for the life of the page and so cannot be
 * waited on the way an ordinary request can:
 *
 * - `requestfinished` waits for the whole body, which for a healthy, still-
 *   open stream never happens at all -- it would hang this wait
 *   indefinitely.
 * - Waiting on `response` alone (which fires promptly, before the body)
 *   avoids the hang, but introduces a different, unrelated false positive:
 *   React 19 StrictMode's dev-only double-invoke opens and immediately
 *   aborts a first, throwaway connection before the real one that persists
 *   -- an intentional, harmless React dev artifact these hooks are already
 *   written to tolerate (`useRunStream`'s and `useNotifications`' reconnect
 *   loops), but indistinguishable from a genuine backend failure using only
 *   "did this request ever get a response."
 *
 * So streams are excluded from both the settle-wait and the failure check
 * below entirely. This does mean a broken *stream* URL specifically is not
 * caught by this file -- but every screen's actual rendered content comes
 * from an ordinary REST call, which this exclusion does not touch.
 */
function isTrackedApiCall(url: string): boolean {
  return isBackendApiUrl(url) && !new URL(url).pathname.includes("/stream");
}

for (const [path, marker] of SCREENS) {
  test(`${path} renders against seeded data`, async ({ page }) => {
    // Signed in BEFORE the listeners below attach: the /login page's own
    // boot effect always tries the httpOnly refresh cookie to see whether a
    // session already exists, and on a brand new browser context that
    // legitimately 401s (there is no cookie yet). That is normal session
    // bootstrapping, not a defect in the screen under test -- so only calls
    // made while loading THIS screen count toward the zero-failures bar.
    await signInAsAdmin(page);

    const failures: string[] = [];
    // `started`/`settled` track every non-stream `/api/` call this screen
    // makes, independent of the `failures` list itself: a request's
    // `response` event (which is what populates `failures`) is not
    // guaranteed to have been processed by Playwright's Node-side listener
    // by the time the DOM update that made the marker visible has been --
    // they are two independent streams of CDP events with no causal link
    // between them. Checking `failures` immediately after the marker
    // appears is a race, not a check (this is exactly how this file
    // originally shipped: it read as a real gate but never once actually
    // caught a broken endpoint, because the response that would have
    // populated `failures` routinely hadn't landed by the time it was
    // read). Waiting until every started call has also `requestfinished`
    // (below) closes that race for every ordinary REST call the screen
    // makes -- see `isTrackedApiCall` above for why streams are handled
    // separately (excluded) rather than waited on the same way.
    let started = 0;
    let settled = 0;
    page.on("request", (r) => {
      if (isTrackedApiCall(r.url())) started++;
    });
    page.on("requestfinished", (r) => {
      if (isTrackedApiCall(r.url())) settled++;
    });
    page.on("requestfailed", (r) => {
      if (!isTrackedApiCall(r.url())) return;
      settled++;
      // A network-level failure (connection refused, aborted, DNS) never
      // produces a `response` event at all, so `failures` alone would
      // never see it -- record it here so a screen that can't even reach
      // the backend fails this gate too, not just one that reaches it and
      // gets a 4xx/5xx back.
      failures.push(`network-error ${r.url()} (${r.failure()?.errorText ?? "unknown"})`);
    });
    page.on("response", (r) => {
      if (isBackendApiUrl(r.url()) && r.status() >= 400) failures.push(`${r.status()} ${r.url()}`);
    });

    await page.goto(path);
    // Chat's composer only renders once a conversation is selected (see
    // fixtures.ts's `startConversation`) -- whether admin already has one
    // depends on what earlier tests/runs did against this seeded database,
    // so always starting a fresh one keeps this deterministic.
    if (path === "/chat") await startConversation(page);
    await expect(page.getByText(marker).first()).toBeVisible();

    // Every call this screen's mount fired is dispatched synchronously
    // during React's initial render, well before any of them can resolve --
    // so by the time the marker (itself dependent on at least one of those
    // calls) is visible, `started` already reflects every call this load
    // will make. This poll just waits for the *responses* Playwright has
    // already committed to deliver to actually arrive at this listener
    // before trusting `failures`.
    await expect.poll(() => settled >= started, { timeout: 15_000 }).toBe(true);

    expect(failures).toEqual([]);
  });
}

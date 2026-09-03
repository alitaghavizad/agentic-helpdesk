import { test, expect } from "@playwright/test";
import { signInAsGuest, startConversation } from "./fixtures";

// A guest has no notification history worth streaming (and no API key is
// needed here since no chat turn is sent), so the notification bell must
// never open GET /api/notifications/stream for a guest session. Verified
// on the network, not just on what's rendered -- a component that renders
// fine while silently holding open a stream it shouldn't would pass a
// purely visual check.
test("a guest reaches chat without ever requesting the notification stream", async ({ page }) => {
  const streamRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/notifications/stream")) streamRequests.push(r.url());
  });

  await signInAsGuest(page);
  await expect(page).toHaveURL(/\/chat$/);
  await startConversation(page);
  await expect(page.getByRole("button", { name: /send/i })).toBeVisible();

  // Give any stream-opening effect a moment to fire before asserting its
  // absence -- the composer being visible doesn't prove the notification
  // hook has finished mounting.
  await page.waitForTimeout(1000);

  expect(streamRequests).toEqual([]);
});

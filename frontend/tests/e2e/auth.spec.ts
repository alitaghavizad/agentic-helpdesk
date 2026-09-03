import { test, expect } from "@playwright/test";
import { signInAsAdmin, signInAsEmployee, signInAsHelpdesk, startConversation } from "./fixtures";

test("admin signs in and reaches the admin panel", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill("admin");
  await page.getByLabel(/password/i).fill("admin");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText(/runs today/i)).toBeVisible();
});

test("a seeded employee signs in and reaches chat", async ({ page }) => {
  await signInAsEmployee(page);
  await expect(page).toHaveURL(/\/chat$/);
  await startConversation(page);
  await expect(page.getByRole("button", { name: /send/i })).toBeVisible();
});

test("a signed-in session survives a page reload", async ({ page }) => {
  // The memory-only access token plus the httpOnly refresh cookie, proven
  // cross-origin (frontend on :5173, backend on the port set by
  // BACKEND_PORT) rather than assumed: a reload wipes the in-memory token
  // entirely, so staying signed in after reload only works if the app
  // silently exchanges the refresh cookie for a new access token.
  await signInAsAdmin(page);
  await page.reload();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText(/runs today/i)).toBeVisible();
});

test("a non-admin is redirected away from /admin", async ({ page }) => {
  await signInAsHelpdesk(page);
  await page.goto("/admin");
  await expect(page).not.toHaveURL(/\/admin$/);
  await expect(page.getByText(/runs today/i)).not.toBeVisible();
});

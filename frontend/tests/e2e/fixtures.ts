import type { Page } from "@playwright/test";

// Seeded accounts, from `backend/tasks.py seed` (126 accounts: 1 admin +
// 100 employee + 25 helpdesk). Credentials come from the repo-root `.env`
// that the live backend under test was started with (ADMIN_PASSWORD,
// SEED_USER_PASSWORD) -- see README.md's Frontend section.
export const ADMIN = { username: "admin", password: "admin" };
export const EMPLOYEE = { username: "alex.hart34", password: "Passw0rd!dev" };
export const HELPDESK = { username: "adam.schmidt", password: "Passw0rd!dev" };

async function signInWithCredentials(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
}

export async function signInAsAdmin(page: Page) {
  await signInWithCredentials(page, ADMIN.username, ADMIN.password);
  await page.waitForURL(/\/admin$/);
}

export async function signInAsEmployee(page: Page) {
  await signInWithCredentials(page, EMPLOYEE.username, EMPLOYEE.password);
  await page.waitForURL(/\/chat$/);
}

export async function signInAsHelpdesk(page: Page) {
  await signInWithCredentials(page, HELPDESK.username, HELPDESK.password);
  await page.waitForURL(/\/tickets$/);
}

export async function signInAsGuest(page: Page, name = "Guest Tester", email = "guest.tester@example.com") {
  await page.goto("/login");
  await page.getByRole("tab", { name: /guest/i }).click();
  await page.getByLabel(/name/i).fill(name);
  await page.getByLabel(/email/i).fill(email);
  await page.getByRole("button", { name: /continue as guest/i }).click();
  await page.waitForURL(/\/chat$/);
}

/**
 * Chat's composer only renders once a conversation is selected -- by
 * design (Chat.test.tsx's first test pins this: the list loading alone
 * must fire no other request). A signed-in visitor otherwise sees the
 * "select a conversation, or start one" empty state, and whether one
 * already exists depends on this account's history, which earlier test
 * runs against the same seeded database can change. Always starting a new
 * one is the real, minimal interaction a user takes to reach the composer,
 * and keeps this deterministic regardless of what ran before it.
 */
export async function startConversation(page: Page) {
  await page.getByRole("button", { name: /new conversation/i }).click();
}

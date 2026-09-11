export type Theme = "light" | "dark";

const STORAGE_KEY = "helpdesk.theme";

/**
 * Reads the stored preference, falling back to the OS setting. Every
 * `localStorage`/`matchMedia` access here is guarded: a private window can
 * make the accessor itself throw, and jsdom has no `matchMedia` at all, and
 * neither is a reason for the whole app to fail to render.
 */
export function preferredTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage unavailable -- fall through to the OS preference.
  }
  try {
    if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) return "dark";
  } catch {
    // No matchMedia (jsdom) -- light is the documented default.
  }
  return "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // A preference we cannot persist still applies for this tab.
  }
}

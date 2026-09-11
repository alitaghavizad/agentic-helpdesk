import { useEffect, useState } from "react";
import { applyTheme, preferredTheme, storeTheme } from "../lib/theme";
import type { Theme } from "../lib/theme";
import { Icon } from "./Icon";

/**
 * Light/dark switch. The class is applied in an effect rather than during
 * render because touching `document` while rendering is a side effect React
 * is free to run twice (StrictMode does exactly that in dev).
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(preferredTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    storeTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      className="grid size-8 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-2 hover:text-ink"
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} className="size-4" />
    </button>
  );
}

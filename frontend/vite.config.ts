// defineConfig comes from "vitest/config", NOT "vite": the plain Vite
// export does not type the `test` key, so a config carrying it fails
// `tsc --noEmit` in step 3 with "Object literal may only specify known
// properties".
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
    // tests/e2e is Playwright's tree (its own `test` from @playwright/test,
    // run via `npx playwright test`), not vitest's -- without this exclude,
    // vitest's default include glob picks up *.spec.ts anywhere and tries
    // to run Playwright tests as vitest tests, which fails immediately.
    exclude: ["**/node_modules/**", "**/dist/**", "tests/e2e/**"],
  },
});

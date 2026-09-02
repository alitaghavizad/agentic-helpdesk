import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RequireRole } from "./RequireRole";
import * as ctx from "./AuthContext";

afterEach(() => vi.restoreAllMocks());

function renderAt(path: string, principal: Partial<ctx.Principal> | null, status = "signed-in") {
  vi.spyOn(ctx, "useAuth").mockReturnValue({
    status, principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chat" element={<div>chat page</div>} />
        <Route path="/login" element={<div>login page</div>} />
        <Route path="/admin" element={<RequireRole role="admin"><div>admin page</div></RequireRole>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireRole", () => {
  it("renders the page for the right role", () => {
    renderAt("/admin", { role: "admin" });
    expect(screen.getByText("admin page")).toBeInTheDocument();
  });

  it("sends a signed-in non-admin to their own landing page, not to login", () => {
    // Bouncing a signed-in employee to /login reads as "you were logged
    // out", which is both wrong and alarming.
    renderAt("/admin", { role: "employee" });
    expect(screen.getByText("chat page")).toBeInTheDocument();
  });

  it("sends a signed-out visitor to login", () => {
    renderAt("/admin", null, "signed-out");
    expect(screen.getByText("login page")).toBeInTheDocument();
  });

  it("renders nothing while the boot refresh is still in flight", () => {
    // Deciding before the refresh resolves would redirect every reload of
    // an admin page to /chat for a moment.
    renderAt("/admin", null, "loading");
    expect(screen.queryByText("admin page")).not.toBeInTheDocument();
    expect(screen.queryByText("login page")).not.toBeInTheDocument();
  });
});

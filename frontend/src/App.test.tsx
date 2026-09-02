import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as ctx from "./auth/AuthContext";

const ADMIN = {
  kind: "user", user_id: "u1", role: "admin", clearance: "privileged",
  department: "IT", employee_ref: null, helpdesk_ref: null,
  username: "admin", full_name: "Administrator",
};

afterEach(() => vi.restoreAllMocks());

describe("App routing", () => {
  it("renders not-found for an unknown path nested under /admin", () => {
    // path="/admin/*" matches and shadows the top-level "*" for anything
    // under /admin, so a typo'd admin sub-route must resolve to its own
    // not-found rather than an empty <Outlet/>.
    vi.spyOn(ctx, "useAuth").mockReturnValue({
      status: "signed-in", principal: ADMIN, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
    } as never);
    render(
      <MemoryRouter initialEntries={["/admin/nonsense"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText(/page not found/i)).toBeInTheDocument();
  });
});

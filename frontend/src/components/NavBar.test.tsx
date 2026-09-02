import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NavBar } from "./NavBar";
import * as ctx from "../auth/AuthContext";

const BASE = {
  kind: "user", user_id: "u1", role: "employee", clearance: "standard",
  department: "Engineering", employee_ref: "EMP-0007", helpdesk_ref: null,
  username: "j.doe", full_name: "Jane Doe",
};

function renderWith(principal: Partial<ctx.Principal>) {
  vi.spyOn(ctx, "useAuth").mockReturnValue({
    status: "signed-in", principal, login: vi.fn(), loginAsGuest: vi.fn(), logout: vi.fn(),
  } as never);
  return render(
    <MemoryRouter>
      <NavBar />
    </MemoryRouter>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("NavBar identity", () => {
  it("shows the principal's full name, not a raw id", () => {
    // The seeded admin (backend/app/db/seed.py) has full_name="Administrator"
    // and neither employee_ref nor helpdesk_ref -- full_name must win over
    // every other fallback or the admin sees a UUID where their name belongs.
    renderWith({ ...BASE, role: "admin", employee_ref: null, full_name: "Administrator", username: "admin" });
    expect(screen.getByText("Administrator")).toBeInTheDocument();
    expect(screen.queryByText("u1")).not.toBeInTheDocument();
  });

  it("falls back to employee_ref only when the server sends neither name field", () => {
    // Defensive path only -- backend/app/auth/router.py always populates
    // full_name today, but this keeps the display honest if that ever lapses.
    renderWith({ ...BASE, full_name: null, username: null });
    expect(screen.getByText("EMP-0007")).toBeInTheDocument();
  });
});

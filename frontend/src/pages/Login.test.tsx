import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import * as ctx from "../auth/AuthContext";
import { Login } from "./Login";

function setup(login = vi.fn(), loginAsGuest = vi.fn()) {
  vi.spyOn(ctx, "useAuth").mockReturnValue({
    status: "signed-out", principal: null, login, loginAsGuest, logout: vi.fn(),
  } as never);
  render(<MemoryRouter><Login /></MemoryRouter>);
  return { login, loginAsGuest };
}

describe("Login", () => {
  it("signs in with username and password", async () => {
    const { login } = setup(vi.fn().mockResolvedValue({ role: "admin" }));
    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "admin");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(login).toHaveBeenCalledWith("admin", "admin");
  });

  it("shows the server's message on bad credentials and keeps the form usable", async () => {
    setup(vi.fn().mockRejectedValue(new ApiError(401, "Invalid username or password")));
    await userEvent.type(screen.getByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/invalid username or password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });

  it("takes a name and email on the guest tab", async () => {
    const { loginAsGuest } = setup(vi.fn(), vi.fn().mockResolvedValue({ role: "guest" }));
    await userEvent.click(screen.getByRole("tab", { name: /guest/i }));
    await userEvent.type(screen.getByLabelText(/name/i), "Dana");
    await userEvent.type(screen.getByLabelText(/email/i), "dana@example.com");
    await userEvent.click(screen.getByRole("button", { name: /continue as guest/i }));
    expect(loginAsGuest).toHaveBeenCalledWith("Dana", "dana@example.com");
  });

  it("warns a guest that their session will not survive a reload", async () => {
    // Guests get no refresh cookie, so this is real behaviour, not a
    // hypothetical -- saying so up front beats a silent logout later.
    setup();
    await userEvent.click(screen.getByRole("tab", { name: /guest/i }));
    expect(screen.getByText(/will not survive|ends when you close/i)).toBeInTheDocument();
  });
});

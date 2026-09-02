import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, landingFor, useAuth } from "./AuthContext";

const fetchMock = vi.fn();
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
const ADMIN = { kind: "user", user_id: "u1", role: "admin", clearance: "privileged",
  department: "IT", employee_ref: null, helpdesk_ref: null, username: "admin", full_name: "Administrator" };

function Probe() {
  const { status, principal } = useAuth();
  return <div data-testid="probe">{status}:{principal?.role ?? "none"}</div>;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

describe("AuthProvider", () => {
  it("restores a session from the refresh cookie on boot", async () => {
    // The access token is memory-only, so a reload starts with nothing and
    // the httpOnly cookie is the ONLY way back into a session.
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith("/api/auth/refresh") ? jsonResponse({ access_token: "t" }) : jsonResponse(ADMIN),
    );
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("signed-in:admin"));
  });

  it("lands signed-out when there is no usable cookie", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "No refresh token" }, 401));
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("signed-out:none"));
  });

  it("never writes the token to storage", async () => {
    // An XSS that reads localStorage would get a bearer token outliving the
    // tab. The cookie it cannot read is the point of the whole design.
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith("/api/auth/refresh") ? jsonResponse({ access_token: "t" }) : jsonResponse(ADMIN),
    );
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("signed-in"));
    expect(JSON.stringify(localStorage)).not.toContain("t");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("landingFor", () => {
  it("routes by role", () => {
    expect(landingFor({ ...ADMIN, role: "admin" })).toBe("/admin");
    expect(landingFor({ ...ADMIN, role: "helpdesk" })).toBe("/tickets");
    expect(landingFor({ ...ADMIN, role: "employee" })).toBe("/chat");
    expect(landingFor({ ...ADMIN, role: "guest" })).toBe("/chat");
  });
});

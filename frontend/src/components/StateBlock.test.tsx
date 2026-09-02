import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { StateBlock, describeError } from "./StateBlock";

describe("StateBlock", () => {
  it("renders the loading state distinguishably", () => {
    render(<StateBlock status="loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the empty state distinguishably", () => {
    render(<StateBlock status="empty" />);
    expect(screen.getByText("Nothing to show yet.")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the error state distinguishably, with role=alert", () => {
    render(<StateBlock status="error" message="Something went wrong. Please try again." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong. Please try again.");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("prefers a custom empty label when given one", () => {
    render(<StateBlock status="empty" emptyLabel="No tickets match this filter." />);
    expect(screen.getByText("No tickets match this filter.")).toBeInTheDocument();
  });
});

describe("describeError", () => {
  it("gives a 403 the access-denied wording rather than the raw detail", () => {
    // A failed request must never fall through and render as an empty
    // table -- and a 403 specifically must not leak FastAPI's raw
    // dependency-raised detail string (spec §6.5's exact phrasing instead).
    const error = new ApiError(403, "Forbidden: admin role required");
    expect(describeError(error)).toBe("You do not have access to this.");
  });

  it("passes through the server's detail for a non-403 ApiError", () => {
    const error = new ApiError(404, "no such ticket");
    expect(describeError(error)).toBe("no such ticket");
  });

  it("falls back to a generic message for a non-ApiError failure", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toBe(
      "Something went wrong. Please try again.",
    );
    expect(describeError("not even an Error")).toBe("Something went wrong. Please try again.");
  });

  it("renders the 403 wording end to end through StateBlock", () => {
    const error = new ApiError(403, "Forbidden");
    render(<StateBlock status="error" message={describeError(error)} />);
    expect(screen.getByRole("alert")).toHaveTextContent("You do not have access to this.");
  });
});

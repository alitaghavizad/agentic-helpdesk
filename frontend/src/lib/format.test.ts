import { describe, expect, it } from "vitest";
import { dateTime, duration, score, tokens, usd } from "./format";

describe("usd", () => {
  it("renders 'unpriced' for null, never a zero", () => {
    // Parent spec 17: a model absent from the rate table stores NULL. A
    // confidently wrong $0.00 is worse than saying nothing.
    expect(usd(null)).toBe("unpriced");
  });

  it("keeps sub-cent costs visible", () => {
    // Most single spans cost well under a cent; rounding to 2dp would
    // render a whole trace as a column of $0.00.
    expect(usd(0.000123)).toBe("$0.000123");
    expect(usd(0)).toBe("$0.000000");
  });

  it("switches to two decimals above a dollar", () => {
    expect(usd(12.3456)).toBe("$12.35");
  });
});

describe("tokens", () => {
  it("groups thousands and renders null as a dash", () => {
    expect(tokens(1234567)).toBe("1,234,567");
    expect(tokens(null)).toBe("—");
  });
});

describe("duration", () => {
  it("renders ms, seconds and minutes at readable precision", () => {
    expect(duration(42)).toBe("42ms");
    expect(duration(1500)).toBe("1.5s");
    expect(duration(95000)).toBe("1m 35s");
    expect(duration(null)).toBe("—");
  });
});

describe("dateTime", () => {
  it("renders a valid ISO string using the platform's locale formatting", () => {
    const iso = "2026-09-01T10:00:00Z";
    expect(dateTime(iso)).toBe(new Date(iso).toLocaleString());
  });

  it("renders a falsy or missing input as a dash", () => {
    expect(dateTime(null)).toBe("—");
    expect(dateTime(undefined)).toBe("—");
    expect(dateTime("")).toBe("—");
  });

  it("renders an unparseable string as a dash rather than 'Invalid Date'", () => {
    expect(dateTime("not-a-date")).toBe("—");
  });
});

describe("score", () => {
  it("rounds a raw floating-point score to two decimals", () => {
    // The exact kind of binary-floating-point noise a routing score can
    // carry off the wire -- 0.87 stored/computed as a float is not always
    // exactly representable.
    expect(score(0.8700000000000001)).toBe("0.87");
  });

  it("renders null/undefined as a dash rather than '0.00'", () => {
    expect(score(null)).toBe("—");
    expect(score(undefined)).toBe("—");
  });
});

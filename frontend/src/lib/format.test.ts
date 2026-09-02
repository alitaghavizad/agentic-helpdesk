import { describe, expect, it } from "vitest";
import { duration, tokens, usd } from "./format";

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

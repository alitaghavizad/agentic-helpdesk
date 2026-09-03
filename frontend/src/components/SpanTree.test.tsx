import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SpanTree } from "./SpanTree";
import type { SpanNode } from "./SpanTree";

function makeSpan(overrides: Partial<SpanNode> = {}): SpanNode {
  return {
    id: "s1",
    kind: "llm",
    name: "root-span",
    status: "ok",
    error: null,
    model: "claude-opus-5",
    duration_ms: 100,
    input_tokens: 10,
    output_tokens: 20,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_usd: 0.05,
    input: { prompt: "hello" },
    output: { text: "hi" },
    children: [],
    ...overrides,
  };
}

describe("SpanTree", () => {
  it("renders nested children indented under their parent", () => {
    const child = makeSpan({ id: "child", name: "child-span" });
    const root = makeSpan({ id: "root", name: "root-span", children: [child] });

    render(<SpanTree roots={[root]} totalMs={100} />);

    const rootItem = screen.getByText("root-span").closest('[role="treeitem"]');
    const childItem = screen.getByText("child-span").closest('[role="treeitem"]');
    expect(rootItem).not.toBeNull();
    expect(childItem).not.toBeNull();
    expect(rootItem).toHaveAttribute("aria-level", "1");
    expect(childItem).toHaveAttribute("aria-level", "2");

    // The child's own row must actually sit further right than the root's,
    // not just carry a different aria-level -- that is what "indented"
    // means visually.
    const rootRow = screen.getByText("root-span").closest("div.flex") as HTMLElement;
    const childRow = screen.getByText("child-span").closest("div.flex") as HTMLElement;
    const rootPad = parseInt(rootRow.style.paddingLeft || "0", 10);
    const childPad = parseInt(childRow.style.paddingLeft || "0", 10);
    expect(childPad).toBeGreaterThan(rootPad);
  });

  it("renders a duration bar proportional to the run total", () => {
    const fast = makeSpan({ id: "fast", name: "fast-span", duration_ms: 25 });
    const slow = makeSpan({ id: "slow", name: "slow-span", duration_ms: 100 });

    render(<SpanTree roots={[fast, slow]} totalMs={100} />);

    const fastBar = screen.getByRole("img", { name: /fast-span duration/ });
    const slowBar = screen.getByRole("img", { name: /slow-span duration/ });
    expect(fastBar.style.width).toBe("25%");
    expect(slowBar.style.width).toBe("100%");
  });

  it("renders 'unpriced' for a null cost_usd and never $0.00", () => {
    const span = makeSpan({ id: "unpriced", name: "unpriced-span", cost_usd: null });

    render(<SpanTree roots={[span]} totalMs={100} />);

    const row = screen.getByText("unpriced-span").closest('[role="treeitem"]') as HTMLElement;
    expect(row).toHaveTextContent("unpriced");
    expect(row).not.toHaveTextContent("$0.00");
  });

  it("collapses and expands a node's redacted input/output", async () => {
    const user = userEvent.setup();
    const span = makeSpan({
      id: "expandable",
      name: "expandable-span",
      input: { redacted_field: "already redacted by the server" },
      output: { answer: "42" },
    });

    render(<SpanTree roots={[span]} totalMs={100} />);

    // Collapsed by default: the redacted payload is not on screen yet.
    expect(screen.queryByText(/already redacted by the server/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /expand expandable-span/i }));
    expect(screen.getByText(/already redacted by the server/)).toBeInTheDocument();
    expect(screen.getByText(/"answer": "42"/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /collapse expandable-span/i }));
    expect(screen.queryByText(/already redacted by the server/)).not.toBeInTheDocument();
  });

  it("renders a span with null duration_ms without breaking the bar layout", () => {
    // totalMs > 0 here (a run that DID finish, e.g. a sibling span
    // completed) so the `totalMs > 0 ? ... : 0` ternary actually reaches
    // the numerator -- pinning this with totalMs=0 (the prior version of
    // this test) would short-circuit before the numerator is ever
    // evaluated, exercising only the divide-by-zero branch.
    //
    // Note on what this can and cannot prove: `node.duration_ms ?? 0` at
    // SpanTree.tsx cannot be proven by any assertion on rendered output --
    // JavaScript's `/` operator already coerces `null` to `0`
    // (`null / 100 === 0`, verified directly), so `(null ?? 0) / 100` and
    // `null / 100` produce the identical `0` and thus the identical "0%"
    // width whether or not the `?? 0` is present. The guard's only real
    // job is satisfying TypeScript's strictNullChecks (`node.duration_ms`
    // is typed `number | null`, and `null / totalMs` does not type-check
    // without it) -- removing it is caught by `npm run typecheck`
    // (TS18047 "possibly null"), not by this or any other runtime test.
    // This test still stands on its own merits: it pins that a null
    // duration_ms renders a 0% bar and a "—" label rather than throwing.
    const span = makeSpan({ id: "nulldur", name: "null-duration-span", duration_ms: null });

    render(<SpanTree roots={[span]} totalMs={100} />);

    const bar = screen.getByRole("img", { name: /null-duration-span duration/ });
    expect(bar.style.width).toBe("0%");
    expect(screen.getByText("null-duration-span")).toBeInTheDocument();
    // The label alongside the bar must also fall back cleanly, not print
    // "nullms" or throw.
    expect(within(bar.closest('[role="treeitem"]') as HTMLElement).getByText("—")).toBeInTheDocument();
  });

  it("renders a deep tree without exceeding the stack", () => {
    const DEPTH = 200;
    let node: SpanNode = makeSpan({ id: "leaf-0", name: "leaf-0", children: [] });
    for (let i = 1; i < DEPTH; i += 1) {
      node = makeSpan({ id: `node-${i}`, name: `node-${i}`, children: [node] });
    }

    expect(() => render(<SpanTree roots={[node]} totalMs={1000} />)).not.toThrow();
    expect(screen.getByText("leaf-0")).toBeInTheDocument();
    expect(screen.getByText(`node-${DEPTH - 1}`)).toBeInTheDocument();
  });
});

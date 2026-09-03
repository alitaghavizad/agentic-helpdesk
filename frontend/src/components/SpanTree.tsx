import { useState } from "react";
import type { components } from "../api/schema";
import { Badge } from "./Badge";
import type { BadgeTone } from "./Badge";
import { JsonBlock } from "./JsonBlock";
import { duration, tokens, usd } from "../lib/format";

export type SpanNode = components["schemas"]["SpanNode"];

const STATUS_TONE: Record<string, BadgeTone> = {
  ok: "success",
  error: "danger",
  denied: "warning",
  running: "info",
  aborted: "neutral",
};

interface SpanRowProps {
  node: SpanNode;
  depth: number;
  totalMs: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
}

/**
 * One row of the waterfall plus its children, recursively. `depth` drives
 * both the visual indent and `aria-level`, so a screen reader announces the
 * same nesting the indentation shows sighted users.
 *
 * `totalMs` is the RUN's total duration (TraceRun.duration_ms), not this
 * node's parent's -- every bar in the tree is proportional to the same
 * whole, the way a flame graph's bars all share one axis. A `duration_ms`
 * of null (span never completed, or the field was never recorded) renders
 * a zero-width bar rather than `NaN%` or a thrown error, and the label
 * still falls through to format.ts's `duration()` so it reads "—".
 */
function SpanRow({ node, depth, totalMs, expanded, onToggle }: SpanRowProps) {
  const isExpanded = expanded.has(node.id);
  const widthPct = totalMs > 0 ? Math.min(100, Math.max(0, ((node.duration_ms ?? 0) / totalMs) * 100)) : 0;

  return (
    <div role="treeitem" aria-level={depth + 1} aria-expanded={isExpanded}>
      <div
        style={{ paddingLeft: `${depth * 16}px` }}
        className="flex flex-wrap items-center gap-2 border-b border-slate-100 py-1.5 text-xs"
      >
        <button
          type="button"
          onClick={() => onToggle(node.id)}
          aria-label={`${isExpanded ? "Collapse" : "Expand"} ${node.name}`}
          className="w-4 shrink-0 text-slate-400 hover:text-slate-700"
        >
          {isExpanded ? "▾" : "▸"}
        </button>
        <Badge tone="neutral">{node.kind}</Badge>
        <span className="font-medium text-slate-800">{node.name}</span>

        <div className="h-2.5 w-24 shrink-0 rounded bg-slate-100">
          <div
            role="img"
            aria-label={`${node.name} duration: ${duration(node.duration_ms)}`}
            className="h-2.5 rounded bg-slate-600"
            style={{ width: `${widthPct}%` }}
          />
        </div>
        <span className="w-16 shrink-0 text-slate-500">{duration(node.duration_ms)}</span>

        <span className="text-slate-500">{node.model ?? "—"}</span>
        <span className="text-slate-500">in {tokens(node.input_tokens)}</span>
        <span className="text-slate-500">out {tokens(node.output_tokens)}</span>
        <span className="text-slate-500">cache-r {tokens(node.cache_read_tokens)}</span>
        <span className="text-slate-500">cache-w {tokens(node.cache_write_tokens)}</span>
        {/* Never a raw "$0.00" for a null cost -- usd(null) reads "unpriced",
            the one thing standing between this and a confidently wrong
            number (parent spec §17). */}
        <span className="font-medium text-slate-700">{usd(node.cost_usd)}</span>

        <Badge tone={STATUS_TONE[node.status] ?? "neutral"}>{node.status}</Badge>
        {node.error && <span className="text-red-700">{node.error}</span>}
      </div>

      {isExpanded && (
        <div style={{ paddingLeft: `${(depth + 1) * 16}px` }} className="border-b border-slate-100 py-2">
          <JsonBlock label="Input" value={node.input} />
          <JsonBlock label="Output" value={node.output} />
        </div>
      )}

      {node.children.length > 0 && (
        <div role="group">
          {node.children.map((child) => (
            <SpanRow key={child.id} node={child} depth={depth + 1} totalMs={totalMs} expanded={expanded} onToggle={onToggle} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The waterfall: a recursive render of `RunTrace.roots`, already a nested
 * tree of `SpanNode` from the server -- this component does not build the
 * tree, only walks it. Reused as-is by Task 8's Conversations detail beside
 * a transcript, so it stays free of anything Traces-page-specific (no
 * routing, no data fetching, no page chrome).
 *
 * `totalMs` is the whole run's duration, shared by every bar in the tree
 * (see SpanRow), and is passed in rather than computed here because the
 * run's own `duration_ms` (TraceRun) is the authoritative total -- summing
 * span durations would double-count anything with concurrent children.
 */
export function SpanTree({ roots, totalMs }: { roots: SpanNode[]; totalMs: number }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function onToggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (roots.length === 0) {
    return <p className="text-sm text-slate-500">No spans recorded for this run.</p>;
  }

  return (
    <div role="tree" aria-label="Span waterfall">
      {roots.map((root) => (
        <SpanRow key={root.id} node={root} depth={0} totalMs={totalMs} expanded={expanded} onToggle={onToggle} />
      ))}
    </div>
  );
}

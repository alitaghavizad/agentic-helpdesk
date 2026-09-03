/**
 * A single labelled JSON value -- used for a span's redacted input/output
 * (SpanTree) and, per this phase's later tasks, an approval's full payload
 * and an audit entry's payload. Redaction (where it applies) happens
 * server-side before the data ever reaches this component; this only
 * formats what it is given and must never claim to be doing any redacting
 * of its own.
 */
export function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text = value === null || value === undefined ? "null" : JSON.stringify(value, null, 2);
  return (
    <div className="mb-2 rounded border border-slate-200 bg-slate-50 p-2">
      <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <pre className="whitespace-pre-wrap break-words text-xs text-slate-700">{text}</pre>
    </div>
  );
}

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
    <div className="mb-2 rounded-lg border border-line bg-surface-2 p-2.5">
      <p className="eyebrow mb-1.5">{label}</p>
      <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-ink-2">{text}</pre>
    </div>
  );
}

import type { ReactNode } from "react";

export interface Column<T> {
  /** Stable identity for the column, used as its React key. */
  key: string;
  header: string;
  render: (row: T) => ReactNode;
}

interface TableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
}

/**
 * Small local table primitive -- no component library per this project's
 * constraints. Deliberately generic over the row type and driven entirely by
 * `columns`/`render` rather than any ticket-specific knowledge, so it can be
 * reused by any future list screen without modification.
 */
export function Table<T>({ columns, rows, rowKey }: TableProps<T>) {
  return (
    <table className="w-full border-collapse text-left text-sm">
      <thead>
        <tr className="border-b border-slate-200 text-xs font-medium uppercase tracking-wide text-slate-500">
          {columns.map((column) => (
            <th key={column.key} scope="col" className="px-3 py-2">
              {column.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)} className="border-b border-slate-100 last:border-b-0 hover:bg-slate-50">
            {columns.map((column) => (
              <td key={column.key} className="px-3 py-2 align-top">
                {column.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

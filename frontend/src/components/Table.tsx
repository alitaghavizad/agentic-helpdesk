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
 *
 * The card surface and the horizontal scroll container both live here
 * rather than at each call site: every list screen wants the same framed
 * table, and the scroll has to be the table's own -- these rows are wide (a
 * trace row alone has nine columns), and letting the whole layout scroll
 * sideways on a narrow viewport takes the nav bar and page header with it.
 */
export function Table<T>({ columns, rows, rowKey }: TableProps<T>) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-line bg-surface-2/60">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="px-3 py-2.5 text-xs font-semibold tracking-wider text-ink-3 uppercase"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className="border-b border-line/60 transition-colors last:border-b-0 hover:bg-surface-2"
            >
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-2.5 align-top text-ink-2">
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

import type { ReactNode } from "react";

export interface Column<T> {
  /** Stable identity for the column, used as its React key. */
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /**
   * Right-align this column's cells and its header. For quantities, money
   * and durations: with tabular figures a right-aligned column puts every
   * decimal point on the same x, so magnitudes can be compared by their
   * ragged left edge without reading a single digit.
   */
  numeric?: boolean;
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
          {/* Deliberately NOT sticky: the card around this table is an
              `overflow-x-auto` scroll container, so a sticky thead would
              stick to a box that never scrolls vertically while the page
              scrolls past it -- inert in exactly the case it looks like it
              handles. The separator is an inset shadow rather than a border
              so it does not add to the header row's height. */}
          <tr className="bg-surface-2 shadow-[inset_0_-1px_0_var(--line)]">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`px-3.5 py-2.5 text-[0.6875rem] font-semibold tracking-[0.06em] text-ink-3 uppercase ${
                  column.numeric ? "text-right" : "text-left"
                }`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            // The hover marker is an inset box-shadow rather than a real
            // left border: a border would change the cell's box width and
            // shift every row's text by 2px as the pointer moves down it.
            <tr
              key={rowKey(row)}
              className="border-b border-line/60 transition-colors last:border-b-0
                hover:bg-surface-2 hover:shadow-[inset_2px_0_0_var(--brand)]"
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-3.5 py-3 align-top text-ink-2 ${
                    column.numeric ? "text-right tabular-nums" : "text-left"
                  }`}
                >
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

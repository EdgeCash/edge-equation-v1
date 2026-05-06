"use client";

/**
 * Reusable sortable + paginated data table with a built-in
 * "Download CSV / JSON" action.
 *
 * Designed for the Data Center rebrand: every human-readable
 * artifact renders inline as a table on the page, and the
 * download buttons stay one click away for power users.
 *
 * Pure client component — no chart library, no virtualised
 * rendering, no third-party data-grid dep. Built to handle
 * a few hundred rows comfortably; bulk feeds (>1k rows) should
 * stay download-only.
 */

import { useMemo, useState } from "react";


export interface DataTableColumn<T> {
  key: keyof T | string;
  header: React.ReactNode;
  /** Render the cell. Defaults to `String(row[key])`. */
  render?: (row: T) => React.ReactNode;
  /** Sort comparator. Defaults to alphanumeric on `row[key]`. */
  sortValue?: (row: T) => string | number;
  align?: "left" | "right";
  className?: string;
}


interface DataTableProps<T> {
  rows: T[];
  columns: DataTableColumn<T>[];
  /** Optional defaults — caller can leave undefined to disable. */
  initialSortKey?: string;
  initialSortDir?: "asc" | "desc";
  pageSize?: number;
  /** Stem for the CSV / JSON download filenames — `<stem>.csv`. */
  downloadStem?: string;
  /** When provided, also exposes a JSON download. Defaults to true. */
  enableJson?: boolean;
  /** Empty-state message when rows is []. */
  emptyLabel?: string;
  /** Optional subtitle rendered above the table (caption). */
  caption?: React.ReactNode;
}


export function DataTable<T extends Record<string, unknown>>({
  rows,
  columns,
  initialSortKey,
  initialSortDir = "desc",
  pageSize,
  downloadStem,
  enableJson = true,
  emptyLabel = "No rows.",
  caption,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | undefined>(initialSortKey);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(initialSortDir);
  const [page, setPage] = useState(1);

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => String(c.key) === sortKey);
    if (!col) return rows;
    const valueOf = col.sortValue ?? defaultSortValue<T>(col.key);
    return rows.slice().sort((a, b) => {
      const av = valueOf(a);
      const bv = valueOf(b);
      if (av === bv) return 0;
      const cmp = av > bv ? 1 : -1;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [rows, sortKey, sortDir, columns]);

  const totalPages = pageSize
    ? Math.max(1, Math.ceil(sorted.length / pageSize))
    : 1;
  const safePage = Math.min(page, totalPages);
  const visible = pageSize
    ? sorted.slice((safePage - 1) * pageSize, safePage * pageSize)
    : sorted;

  return (
    <div className="chalk-card overflow-hidden">
      {(caption || downloadStem) && (
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-chalkboard-700/60">
          <div className="text-xs text-chalk-300">
            {caption ?? (
              <span className="text-chalk-500">
                {sorted.length} row{sorted.length === 1 ? "" : "s"}
              </span>
            )}
          </div>
          {downloadStem && rows.length > 0 && (
            <div className="flex items-center gap-2">
              <DownloadButton
                kind="csv"
                rows={rows}
                columns={columns}
                stem={downloadStem}
              />
              {enableJson && (
                <DownloadButton
                  kind="json"
                  rows={rows}
                  columns={columns}
                  stem={downloadStem}
                />
              )}
            </div>
          )}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((c) => {
                const k = String(c.key);
                const active = sortKey === k;
                return (
                  <th
                    key={k}
                    className={
                      (c.align === "right" ? "text-right " : "")
                      + (c.className ?? "")
                    }
                  >
                    <button
                      type="button"
                      onClick={() => {
                        if (sortKey === k) {
                          setSortDir((d) => (d === "asc" ? "desc" : "asc"));
                        } else {
                          setSortKey(k);
                          setSortDir(initialSortDir);
                        }
                        setPage(1);
                      }}
                      className={
                        "inline-flex items-center gap-1 hover:text-elite transition-colors "
                        + (active ? "text-elite" : "text-chalk-300")
                      }
                    >
                      {c.header}
                      {active && (
                        <span aria-hidden className="text-[9px]">
                          {sortDir === "asc" ? "▲" : "▼"}
                        </span>
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="text-chalk-100">
            {visible.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="text-center text-chalk-500 py-8"
                >
                  {emptyLabel}
                </td>
              </tr>
            ) : (
              visible.map((r, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td
                      key={String(c.key)}
                      className={
                        (c.align === "right" ? "text-right " : "")
                        + (c.className ?? "")
                      }
                    >
                      {c.render
                        ? c.render(r)
                        : String(r[c.key as keyof T] ?? "—")}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {pageSize && totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-chalkboard-700/60 text-xs text-chalk-300">
          <span>
            Page <span className="font-mono text-chalk-100">{safePage}</span>{" "}
            of <span className="font-mono text-chalk-100">{totalPages}</span>
          </span>
          <div className="flex items-center gap-2">
            <PagerButton
              disabled={safePage <= 1}
              onClick={() => setPage(safePage - 1)}
            >
              ← Prev
            </PagerButton>
            <PagerButton
              disabled={safePage >= totalPages}
              onClick={() => setPage(safePage + 1)}
            >
              Next →
            </PagerButton>
          </div>
        </div>
      )}
    </div>
  );
}


/* ---------- helpers ---------- */


function defaultSortValue<T extends Record<string, unknown>>(
  key: keyof T | string,
): (row: T) => string | number {
  return (row) => {
    const v = row[key as keyof T];
    if (typeof v === "number") return v;
    if (v === null || v === undefined) return "";
    return String(v);
  };
}


function PagerButton({
  disabled, onClick, children,
}: {
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={
        "px-3 py-1 rounded border transition-colors "
        + (disabled
          ? "border-chalkboard-800/60 text-chalk-500 cursor-not-allowed"
          : "border-chalkboard-700/60 text-chalk-100 hover:text-elite hover:border-elite/40")
      }
    >
      {children}
    </button>
  );
}


function DownloadButton<T extends Record<string, unknown>>({
  kind, rows, columns, stem,
}: {
  kind: "csv" | "json";
  rows: T[];
  columns: DataTableColumn<T>[];
  stem: string;
}) {
  const onClick = () => {
    const blob =
      kind === "csv"
        ? new Blob([rowsToCsv(rows, columns)], { type: "text/csv" })
        : new Blob([JSON.stringify(rows, null, 2)], {
            type: "application/json",
          });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${stem}.${kind}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded border border-chalkboard-700/60 text-chalk-300 hover:text-elite hover:border-elite/40 transition-colors"
    >
      Download {kind.toUpperCase()}
    </button>
  );
}


function rowsToCsv<T extends Record<string, unknown>>(
  rows: T[], columns: DataTableColumn<T>[],
): string {
  const headers = columns.map((c) => csvEscape(headerLabel(c)));
  const lines = [headers.join(",")];
  for (const row of rows) {
    const cells = columns.map((c) => {
      const valueOf = c.sortValue ?? defaultSortValue<T>(c.key);
      const v = valueOf(row);
      return csvEscape(typeof v === "number" ? String(v) : (v ?? ""));
    });
    lines.push(cells.join(","));
  }
  return lines.join("\n") + "\n";
}


function headerLabel<T>(col: DataTableColumn<T>): string {
  if (typeof col.header === "string") return col.header;
  return String(col.key);
}


function csvEscape(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

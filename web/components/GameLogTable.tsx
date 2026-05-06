"use client";

import { useMemo, useState } from "react";

import type { PlayerGameLog, PlayerGameLogRow } from "../lib/player-data";


interface GameLogTableProps {
  log: PlayerGameLog | null;
  emptyLabel?: string;
}


/**
 * Renders the player's game logs (last 5/10/20). Stat columns are
 * derived from the union of keys across rows so a future schema
 * tweak (adding `OBP`, `SLG`, …) shows up automatically.
 *
 * When the per-sport pipeline hasn't shipped game logs yet, the
 * caller passes `log = null` and the component renders an honest
 * "Limited data" panel instead of fabricated rows.
 */
export function GameLogTable({
  log,
  emptyLabel =
    "Limited data — game logs not yet available for this sport. "
    + "Populated automatically when the engine pipeline starts "
    + "shipping `<sport>/player_logs/<slug>.json`.",
}: GameLogTableProps) {
  const [windowSize, setWindowSize] = useState<5 | 10 | 20>(10);

  const sliced = useMemo(() => {
    if (!log) return [];
    return (log.rows ?? []).slice(0, windowSize);
  }, [log, windowSize]);

  const statKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const r of sliced) {
      for (const k of Object.keys(r.stats ?? {})) keys.add(k);
    }
    return Array.from(keys);
  }, [sliced]);

  if (!log || (log.rows ?? []).length === 0) {
    return (
      <div className="rounded border border-chalkboard-700/60 bg-chalkboard-900/60 p-4 text-sm text-chalk-300">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-[10px] uppercase tracking-wider text-chalk-500 mr-1">
          Window
        </span>
        {[5, 10, 20].map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => setWindowSize(n as 5 | 10 | 20)}
            className={
              "text-[11px] font-mono px-2 py-1 rounded transition-colors "
              + (windowSize === n
                ? "bg-elite/20 text-elite border border-elite/50"
                : "bg-chalkboard-800/60 text-chalk-300 border border-chalkboard-700/60 hover:text-elite hover:border-elite/40")
            }
          >
            Last {n}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-chalk-500">
          {Math.min(sliced.length, log.rows.length)} of {log.rows.length} games
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Opp</th>
              <th>Result</th>
              {statKeys.map((k) => (
                <th key={k} className="text-right">
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="text-chalk-100">
            {sliced.map((r, i) => (
              <Row key={`${r.date}-${i}`} row={r} statKeys={statKeys} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function Row({
  row, statKeys,
}: { row: PlayerGameLogRow; statKeys: string[] }) {
  const oppLabel = `${row.is_home ? "vs" : "@"} ${row.opponent || "—"}`;
  return (
    <tr>
      <td className="font-mono text-xs text-chalk-300 whitespace-nowrap">
        {row.date || "—"}
      </td>
      <td className="text-chalk-100 text-xs">{oppLabel}</td>
      <td>
        <ResultBadge result={row.result} />
      </td>
      {statKeys.map((k) => (
        <td key={k} className="font-mono text-xs text-right">
          {formatStat(row.stats?.[k])}
        </td>
      ))}
    </tr>
  );
}


function ResultBadge({ result }: { result: PlayerGameLogRow["result"] }) {
  if (result === null || result === undefined) {
    return <span className="text-chalk-500 text-xs">—</span>;
  }
  const r = String(result).toUpperCase();
  const color =
    r === "W"
      ? "text-strong"
      : r === "L"
        ? "text-nosignal"
        : "text-chalk-300";
  return <span className={`font-mono text-xs ${color}`}>{r}</span>;
}


function formatStat(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  return String(v);
}

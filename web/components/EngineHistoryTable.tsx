/**
 * Engine pick history table — renders graded picks from the
 * CLV-tracker log on a player or team. Honest "Limited Data"
 * placeholder when the corpus is empty (see profiles.ts for the
 * loader rules).
 */

import type { PickHistoryRow } from "../lib/profiles";


interface EngineHistoryTableProps {
  rows: PickHistoryRow[];
}


export function EngineHistoryTable({ rows }: EngineHistoryTableProps) {
  if (!rows || rows.length === 0) {
    return (
      <div className="rounded border border-chalkboard-700/60 bg-chalkboard-900/60 p-4 text-sm text-chalk-300">
        Limited data — no graded engine picks have been logged for
        this name yet. Newly tracked picks will appear here as they
        settle.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Matchup</th>
            <th>Pick</th>
            <th>Edge</th>
            <th>CLV</th>
            <th>Result</th>
            <th>Units</th>
          </tr>
        </thead>
        <tbody className="text-chalk-100">
          {rows.map((r, i) => (
            <tr key={`${r.date}-${i}`}>
              <td className="font-mono text-xs text-chalk-300">
                {r.date || "—"}
              </td>
              <td>{r.matchup || "—"}</td>
              <td>
                <div className="text-elite font-mono text-xs">{r.pick}</div>
                <div className="text-[10px] uppercase tracking-wider text-chalk-500">
                  {r.bet_type}
                </div>
              </td>
              <td className="font-mono text-xs">
                {typeof r.edge_pp === "number"
                  ? `${r.edge_pp >= 0 ? "+" : ""}${r.edge_pp.toFixed(2)}pp`
                  : "—"}
              </td>
              <td className="font-mono text-xs">
                {typeof r.clv_pp === "number" ? (
                  <span
                    className={r.clv_pp >= 0 ? "text-strong" : "text-nosignal"}
                  >
                    {r.clv_pp >= 0 ? "+" : ""}
                    {r.clv_pp.toFixed(2)}pp
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td>
                <ResultBadge result={r.result ?? null} />
              </td>
              <td className="font-mono text-xs">
                {typeof r.units === "number"
                  ? `${r.units >= 0 ? "+" : ""}${r.units.toFixed(2)}u`
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function ResultBadge({
  result,
}: { result: "WIN" | "LOSS" | "PUSH" | null }) {
  if (result === null) {
    return <span className="text-chalk-500 text-xs">Pending</span>;
  }
  const color =
    result === "WIN"
      ? "text-strong"
      : result === "LOSS"
        ? "text-nosignal"
        : "text-chalk-300";
  return <span className={`font-mono text-xs ${color}`}>{result}</span>;
}

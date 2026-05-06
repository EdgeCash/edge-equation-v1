import type { FeedPick, SportKey } from "../lib/feed";


interface TodayPicksSidebarProps {
  picks: FeedPick[];
  sport: SportKey;
}


/** Compact sidebar listing of today's picks involving this player or
 * team. Empty state stays honest: "No picks today." */
export function TodayPicksSidebar({ picks, sport }: TodayPicksSidebarProps) {
  if (!picks || picks.length === 0) {
    return (
      <div className="chalk-card p-4 text-sm text-chalk-300">
        <p className="font-mono uppercase tracking-wider text-xs text-chalk-500 mb-2">
          Today
        </p>
        <p>No picks on today&apos;s {sport.toUpperCase()} card.</p>
      </div>
    );
  }
  return (
    <div className="chalk-card p-4">
      <p className="font-mono uppercase tracking-wider text-xs text-chalk-500 mb-3">
        Today · {sport.toUpperCase()}
      </p>
      <ul className="space-y-3">
        {picks.map((p) => (
          <li
            key={p.id}
            className="text-sm border-l-2 border-elite/60 pl-3"
          >
            <p className="text-chalk-50 font-medium">{p.selection}</p>
            <p className="text-[10px] uppercase tracking-wider text-chalk-500 mt-1">
              {p.market_type.replace(/_/g, " ")}
            </p>
            <div className="mt-1 flex items-center gap-3 text-xs font-mono text-chalk-300">
              <span>edge {prettyPct(p.edge)}</span>
              <span>·</span>
              <span>fair {prettyPct(p.fair_prob)}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}


function prettyPct(raw: string | null | undefined): string {
  if (!raw) return "—";
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) < 1) {
    return `${(n * 100).toFixed(1)}%`;
  }
  return `${n.toFixed(1)}%`;
}

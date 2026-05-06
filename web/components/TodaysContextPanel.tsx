/**
 * Today's Context — live snapshot panel for player profiles.
 *
 * Shows the values the engine actually used for today's projection
 * (lineup spot, opponent matchup, weather, injury status, …) when
 * the per-sport pipeline writes
 * `public/data/<sport>/context_today/<slug>.json`. Until then,
 * renders a "Limited data" panel — the same honest empty state the
 * rest of the site uses.
 */

import type { PlayerContextSnapshot } from "../lib/player-data";


interface TodaysContextPanelProps {
  snapshot: PlayerContextSnapshot | null;
  emptyLabel?: string;
}


export function TodaysContextPanel({
  snapshot,
  emptyLabel =
    "Limited data — today's live context (lineup spot, opponent, "
    + "weather, injury status) hasn't been wired through yet for this "
    + "sport. Populated automatically when the engine pipeline starts "
    + "shipping `<sport>/context_today/<slug>.json`.",
}: TodaysContextPanelProps) {
  if (!snapshot || (snapshot.items ?? []).length === 0) {
    return (
      <div className="rounded border border-chalkboard-700/60 bg-chalkboard-900/60 p-4 text-sm text-chalk-300">
        {emptyLabel}
      </div>
    );
  }
  return (
    <div>
      {snapshot.as_of && (
        <p className="text-[10px] uppercase tracking-wider text-chalk-500 font-mono mb-3">
          Snapshot · {snapshot.as_of}
        </p>
      )}
      <ul className="grid sm:grid-cols-2 gap-3 text-sm text-chalk-300">
        {snapshot.items.map((item) => (
          <li
            key={item.label}
            className="border border-chalkboard-700/60 rounded-md p-3"
          >
            <p className="text-[10px] uppercase tracking-wider text-chalk-500">
              {item.label}
            </p>
            <p className="mt-1 text-chalk-100">{item.value}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

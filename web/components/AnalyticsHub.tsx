/**
 * Analytics Hub — homepage tile grid.
 *
 * Renders four cross-sport tiles populated from each sport's
 * backtest summary JSON + the unified daily feed:
 *
 *   - Today's snapshot (per-sport pick count + status badge)
 *   - Trending players (counted from today's prop picks across sports)
 *   - Hot streaks (engine ROI by sport, last 200 picks)
 *   - Model accuracy by sport (Brier from per-sport backtest)
 *
 * Each tile is a pure server component — no client JS at all so the
 * homepage stays fast.
 */

import Link from "next/link";

import {
  BacktestSummary,
  DailyFeed,
  SPORTS,
  SPORT_LABEL,
  SportKey,
  picksForSport,
  gameParlaysForSport,
  propParlaysForSport,
} from "../lib/feed";
import { parseSelection, slugify } from "../lib/search-index";


interface AnalyticsHubProps {
  feed: DailyFeed | null;
  snapshots: Partial<Record<SportKey, BacktestSummary | null>>;
}


export function AnalyticsHub({ feed, snapshots }: AnalyticsHubProps) {
  return (
    <section className="max-w-7xl mx-auto px-4 sm:px-6 py-12">
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Analytics Hub
          </p>
          <h2 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            Across every sport, in one view.
          </h2>
        </div>
        {feed?.footer && (
          <p className="text-xs text-chalk-500 font-mono">
            {feed.footer}
          </p>
        )}
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <TodaysSnapshot feed={feed} />
        <TrendingPlayers feed={feed} />
        <HotStreaks snapshots={snapshots} />
        <ModelAccuracy snapshots={snapshots} />
      </div>
    </section>
  );
}


function TodaysSnapshot({ feed }: { feed: DailyFeed | null }) {
  return (
    <div className="chalk-card p-5">
      <p className="text-[11px] uppercase tracking-wider text-chalk-500 font-mono">
        Today&apos;s snapshot
      </p>
      <ul className="mt-3 space-y-2">
        {SPORTS.map((sport) => {
          const picks = picksForSport(feed, sport);
          const gameP = gameParlaysForSport(feed, sport).length;
          const propP = propParlaysForSport(feed, sport).length;
          const status = feed?.market_status?.[sport] ?? "—";
          return (
            <li
              key={sport}
              className="flex items-center justify-between text-sm"
            >
              <span className="text-chalk-100 font-medium">
                {SPORT_LABEL[sport]}
              </span>
              <span className="font-mono text-xs text-chalk-300">
                {picks.length} picks · {gameP + propP} parlays
                {status !== "OK" && status !== "—" && (
                  <span className="ml-2 text-moderate">[{status}]</span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <Link
        href="/daily-card"
        className="mt-4 inline-block text-xs text-elite hover:underline"
      >
        Open today&apos;s card →
      </Link>
    </div>
  );
}


function TrendingPlayers({ feed }: { feed: DailyFeed | null }) {
  const counts = new Map<
    string,
    { sport: SportKey; display: string; n: number }
  >();
  for (const sport of SPORTS) {
    const picks = picksForSport(feed, sport);
    for (const p of picks) {
      const { player } = parseSelection(p);
      if (!player) continue;
      const key = `${sport}:${slugify(player)}`;
      const prev = counts.get(key);
      if (prev) prev.n += 1;
      else counts.set(key, { sport, display: player, n: 1 });
    }
  }
  const top = Array.from(counts.values())
    .sort((a, b) => b.n - a.n)
    .slice(0, 5);

  return (
    <div className="chalk-card p-5">
      <p className="text-[11px] uppercase tracking-wider text-chalk-500 font-mono">
        Trending players today
      </p>
      {top.length === 0 ? (
        <p className="mt-3 text-sm text-chalk-300">
          No qualifying prop picks today across the engines.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {top.map((row) => (
            <li key={`${row.sport}-${row.display}`} className="text-sm">
              <Link
                href={`/player/${row.sport}/${slugify(row.display)}`}
                className="flex items-center justify-between hover:text-elite transition-colors"
              >
                <span className="text-chalk-100">{row.display}</span>
                <span className="font-mono text-xs text-chalk-300">
                  {row.n} pick{row.n === 1 ? "" : "s"} · {SPORT_LABEL[row.sport]}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function HotStreaks({
  snapshots,
}: { snapshots: Partial<Record<SportKey, BacktestSummary | null>> }) {
  const ranked = SPORTS
    .map((sport) => {
      const snap = snapshots[sport];
      const game = snap?.parlays?.game_results;
      const props = snap?.parlays?.player_props;
      const roi =
        game?.roi_pct !== undefined && props?.roi_pct !== undefined
          ? (game.roi_pct + props.roi_pct) / 2
          : game?.roi_pct ?? props?.roi_pct ?? null;
      return { sport, roi };
    })
    .filter((r): r is { sport: SportKey; roi: number } => r.roi !== null)
    .sort((a, b) => b.roi - a.roi);

  return (
    <div className="chalk-card p-5">
      <p className="text-[11px] uppercase tracking-wider text-chalk-500 font-mono">
        Engine hot streaks
      </p>
      {ranked.length === 0 ? (
        <p className="mt-3 text-sm text-chalk-300">
          Backtest snapshots not available yet.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {ranked.map((row) => (
            <li
              key={row.sport}
              className="flex items-center justify-between text-sm"
            >
              <span className="text-chalk-100 font-medium">
                {SPORT_LABEL[row.sport]}
              </span>
              <span
                className={
                  "font-mono text-xs " +
                  (row.roi > 0 ? "text-strong" : "text-nosignal")
                }
              >
                {row.roi >= 0 ? "+" : ""}
                {row.roi.toFixed(1)}% avg parlay ROI
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[10px] text-chalk-500">
        Average of game-results + player-props parlay ROI from each
        sport&apos;s walk-forward backtest.
      </p>
    </div>
  );
}


function ModelAccuracy({
  snapshots,
}: { snapshots: Partial<Record<SportKey, BacktestSummary | null>> }) {
  return (
    <div className="chalk-card p-5">
      <p className="text-[11px] uppercase tracking-wider text-chalk-500 font-mono">
        Model accuracy (Brier, lower = better)
      </p>
      <ul className="mt-3 space-y-2">
        {SPORTS.map((sport) => {
          const snap = snapshots[sport];
          const brier =
            snap?.parlays?.game_results?.brier
            ?? snap?.parlays?.player_props?.brier
            ?? null;
          return (
            <li
              key={sport}
              className="flex items-center justify-between text-sm"
            >
              <span className="text-chalk-100 font-medium">
                {SPORT_LABEL[sport]}
              </span>
              <span className="font-mono text-xs text-chalk-300">
                {brier !== null ? brier.toFixed(3) : "—"}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[10px] text-chalk-500">
        From the per-sport backtest summary. Brier ≤ 0.25 is the
        publish gate; below 0.246 is the production-quality bar.
      </p>
    </div>
  );
}

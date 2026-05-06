/**
 * Per-sport hub — /sport/<sport>
 *
 * Server-rendered. One landing page per sport that surfaces:
 *
 *   - Today's pick count + market status badge.
 *   - Both strict-policy parlay sub-sections (game-results +
 *     player-props), or the audit's "No qualified parlay today" line.
 *   - Quick-glance backtest snapshot from the per-sport
 *     `<sport>/backtest_summary.json` written by the engine's
 *     backtest CLI.
 *   - Today's per-row picks list (clickable into player/team
 *     profiles via the search-index slug rules).
 *
 * Honest "Limited Data" messaging when a sport's pipeline hasn't
 * shipped today's outputs yet — same rule the per-sport feeds use.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { ChalkboardBackground } from "../../../components/ChalkboardBackground";
import { MetricTip } from "../../../components/MetricTip";
import { ParlayCard } from "../../../components/ParlayCard";
import { TransparencyNote } from "../../../components/TransparencyNote";
import {
  BacktestSummary,
  DailyFeed,
  FeedPick,
  SPORTS,
  SPORT_LABEL,
  SportKey,
  gameParlaysForSport,
  getBacktestSummary,
  getDailyFeed,
  picksForSport,
  propParlaysForSport,
} from "../../../lib/feed";
import { parseSelection, slugify } from "../../../lib/search-index";


export const dynamic = "force-dynamic";


interface RouteParams {
  params: Promise<{ sport: string }>;
}


export default async function SportHubPage({ params }: RouteParams) {
  const { sport: sportRaw } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) notFound();

  const feed = await getDailyFeed();
  const snap = await getBacktestSummary(sport);
  const picks = picksForSport(feed, sport);
  const gameParlays = gameParlaysForSport(feed, sport);
  const propParlays = propParlaysForSport(feed, sport);
  const status = feed?.market_status?.[sport] ?? null;
  const noQualified = collectNoQualifiedMessages(feed, sport);

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <div className="flex flex-wrap items-baseline gap-3">
            <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
              Today
            </p>
            <h1 className="text-3xl sm:text-4xl font-bold text-chalk-50">
              {SPORT_LABEL[sport]} hub
            </h1>
            {status && status !== "OK" && (
              <span className="font-mono text-xs px-2 py-1 rounded border border-moderate/40 text-moderate bg-moderate/10">
                {status}
              </span>
            )}
          </div>
          <p className="mt-3 text-sm text-chalk-300 max-w-2xl">
            {summarisePlay(picks, gameParlays.length, propParlays.length)}
          </p>
          {feed?.footer && (
            <p className="mt-2 text-[11px] text-chalk-500 font-mono">
              {feed.footer}
            </p>
          )}
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-8">
          <ParlaySection
            title={`${SPORT_LABEL[sport]} game-results parlay`}
            parlays={gameParlays}
            sport={sport}
            universe="game_results"
            noQualifiedMessage={noQualified.game_results ?? null}
          />
          <ParlaySection
            title={`${SPORT_LABEL[sport]} player-props parlay`}
            parlays={propParlays}
            sport={sport}
            universe="player_props"
            noQualifiedMessage={noQualified.player_props ?? null}
          />

          <PicksList picks={picks} sport={sport} />
        </div>

        <aside className="space-y-6">
          <BacktestSnapshot snap={snap} sport={sport} />
          <SportSwitcher current={sport} />
          <Link
            href="/parlays"
            className="block chalk-card p-4 text-xs text-chalk-300 hover:text-elite transition-colors"
          >
            View every qualifying parlay across sports →
          </Link>
        </aside>
      </section>

      <TransparencyNote />
    </>
  );
}


/* ---------- helpers ---------- */


function summarisePlay(
  picks: FeedPick[],
  gameParlays: number,
  propParlays: number,
): string {
  const parts: string[] = [];
  parts.push(
    picks.length === 0
      ? "No qualified per-row picks today."
      : `${picks.length} qualified pick${picks.length === 1 ? "" : "s"} today.`,
  );
  if (gameParlays + propParlays > 0) {
    parts.push(
      `${gameParlays + propParlays} qualified parlay ticket${
        gameParlays + propParlays === 1 ? "" : "s"
      } across both engines.`,
    );
  } else {
    parts.push("No qualified parlay today across either engine.");
  }
  return parts.join(" ");
}


function collectNoQualifiedMessages(
  feed: DailyFeed | null,
  sport: SportKey,
): { game_results?: string; player_props?: string } {
  if (!feed) return {};
  if (sport === "mlb") {
    return (feed.parlays?.no_qualified_message as
      | { game_results?: string; player_props?: string }
      | undefined) ?? {};
  }
  return (
    (feed[sport]?.parlays?.no_qualified_message as
      | { game_results?: string; player_props?: string }
      | undefined) ?? {}
  );
}


function ParlaySection({
  title, parlays, sport, universe, noQualifiedMessage,
}: {
  title: string;
  parlays: import("../../../lib/feed").FeedParlay[];
  sport: SportKey;
  universe: "game_results" | "player_props";
  noQualifiedMessage: string | null;
}) {
  return (
    <div>
      <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
        {title}
      </h2>
      {parlays.length === 0 ? (
        <div className="chalk-card p-5 text-sm text-chalk-300">
          <p>
            <MetricTip term="no_qualified" label="No qualified parlay today" />
            {" — "}
            {noQualifiedMessage ?? "data does not support a high-confidence combination."}
          </p>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {parlays.map((p) => (
            <ParlayCard
              key={p.id}
              parlay={p}
              sport={sport}
              universe={universe}
            />
          ))}
        </div>
      )}
    </div>
  );
}


function PicksList({ picks, sport }: { picks: FeedPick[]; sport: SportKey }) {
  if (picks.length === 0) {
    return (
      <div className="chalk-card p-5 text-sm text-chalk-300">
        <h2 className="text-base font-semibold text-chalk-50 mb-2">
          Today&apos;s per-row picks
        </h2>
        <p>
          No qualified single-leg picks today. Engine ran but
          produced nothing above the per-market thresholds.
        </p>
      </div>
    );
  }
  return (
    <div className="chalk-card p-5">
      <h2 className="text-base font-semibold text-chalk-50 mb-4">
        Today&apos;s per-row picks
      </h2>
      <ul className="space-y-3">
        {picks.map((p) => {
          const { player, team } = parseSelection(p);
          const profileLink = player
            ? `/player/${sport}/${slugify(player)}`
            : team
              ? `/team/${sport}/${slugify(team)}`
              : null;
          return (
            <li
              key={p.id}
              className="border-l-2 border-elite/50 pl-3 text-sm"
            >
              <p className="text-chalk-50 font-medium">
                {profileLink ? (
                  <Link
                    href={profileLink}
                    className="hover:text-elite transition-colors"
                  >
                    {p.selection}
                  </Link>
                ) : (
                  p.selection
                )}
              </p>
              <p className="text-[10px] uppercase tracking-wider text-chalk-500 mt-1">
                {p.market_type.replace(/_/g, " ")} · tier {p.tier ?? "—"} ·
                edge {prettyEdge(p.edge)} · {prettyOdds(p.line.odds)}
              </p>
              {p.notes && (
                <p className="mt-1 text-xs text-chalk-300">{p.notes}</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}


function BacktestSnapshot({
  snap, sport,
}: { snap: BacktestSummary | null; sport: SportKey }) {
  if (!snap) {
    return (
      <div className="chalk-card p-4 text-xs text-chalk-300">
        <p className="font-mono uppercase tracking-wider text-chalk-500 mb-2">
          {SPORT_LABEL[sport]} engine snapshot
        </p>
        <p>Backtest snapshot not available yet.</p>
      </div>
    );
  }
  const game = snap.parlays?.game_results;
  const props = snap.parlays?.player_props;
  return (
    <div className="chalk-card p-4 text-xs">
      <p className="font-mono uppercase tracking-wider text-chalk-300 mb-3">
        {SPORT_LABEL[sport]} engine snapshot
      </p>
      <ul className="space-y-2 text-chalk-100">
        {game && (
          <li>
            Game-results parlay{" "}
            <MetricTip term="roi" label="ROI" />:{" "}
            <strong className="text-elite">
              {game.roi_pct >= 0 ? "+" : ""}
              {game.roi_pct.toFixed(1)}%
            </strong>{" "}
            <span className="text-chalk-500">
              ({game.n_tickets} tickets ·{" "}
              <MetricTip term="brier" label="Brier" />{" "}
              {game.brier.toFixed(3)})
            </span>
          </li>
        )}
        {props && (
          <li>
            Player-props parlay{" "}
            <MetricTip term="roi" label="ROI" />:{" "}
            <strong className="text-elite">
              {props.roi_pct >= 0 ? "+" : ""}
              {props.roi_pct.toFixed(1)}%
            </strong>{" "}
            <span className="text-chalk-500">
              ({props.n_tickets} tickets · Brier {props.brier.toFixed(3)})
            </span>
          </li>
        )}
      </ul>
      <details className="mt-3">
        <summary className="cursor-pointer text-[11px] text-chalk-300 hover:text-elite">
          Per-market breakdown
        </summary>
        <ul className="mt-2 space-y-1">
          {Object.entries(snap.per_market ?? {}).map(([key, row]) => (
            <li
              key={key}
              className="flex items-center justify-between gap-2 text-[11px] text-chalk-100"
            >
              <span>{key.replace(/_/g, " ")}</span>
              <span className="font-mono text-chalk-300">
                n={row.n} ·{" "}
                <span className={row.roi_pct > 0 ? "text-strong" : "text-nosignal"}>
                  {row.roi_pct >= 0 ? "+" : ""}
                  {row.roi_pct.toFixed(1)}%
                </span>
              </span>
            </li>
          ))}
        </ul>
      </details>
      <p className="mt-3 text-[10px] text-chalk-500">
        Windows: {snap.windows.join(" / ")} · updated{" "}
        {snap.generated_at
          ? new Date(snap.generated_at).toLocaleDateString()
          : "—"}
      </p>
      {snap.feature_flag && (
        <p className="mt-2 text-[10px] text-chalk-500">
          Production flag:{" "}
          <code className="text-chalk-300">{snap.feature_flag.name}</code>{" "}
          ({snap.feature_flag.default})
        </p>
      )}
    </div>
  );
}


function SportSwitcher({ current }: { current: SportKey }) {
  return (
    <div className="chalk-card p-4 text-sm">
      <p className="font-mono uppercase tracking-wider text-xs text-chalk-500 mb-3">
        Other sports
      </p>
      <ul className="grid grid-cols-2 gap-2">
        {SPORTS.filter((s) => s !== current).map((s) => (
          <li key={s}>
            <Link
              href={`/sport/${s}`}
              className="block px-3 py-2 rounded border border-chalkboard-700/60 text-chalk-100 hover:text-elite hover:border-elite/40 transition-colors text-sm"
            >
              {SPORT_LABEL[s]}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}


function prettyEdge(raw: string): string {
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return "—";
  const pp = n * 100;
  return `${pp >= 0 ? "+" : ""}${pp.toFixed(1)}pp`;
}


function prettyOdds(odds: number | null | undefined): string {
  if (odds === null || odds === undefined || !Number.isFinite(odds)) return "—";
  return odds > 0 ? `+${Math.round(odds)}` : `${Math.round(odds)}`;
}

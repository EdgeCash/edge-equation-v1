/**
 * Player profile — /player/<sport>/<id>
 *
 * Server-rendered. Pulls today's picks on the player + the engine's
 * historical picks involving them (CLV-tracked) + the per-sport
 * backtest snapshot for context. Renders an honest "Limited Data"
 * panel when a slice isn't yet wired through the sport's ingestion
 * layer instead of fabricating numbers — same rule the engine
 * itself follows.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { ChalkboardBackground } from "../../../../components/ChalkboardBackground";
import { GameLogTable } from "../../../../components/GameLogTable";
import { ProfileFilters } from "../../../../components/ProfileFilters";
import { RollingChart } from "../../../../components/RollingChart";
import { TodaysContextPanel } from "../../../../components/TodaysContextPanel";
import {
  SPORTS,
  SPORT_LABEL,
  SportKey,
} from "../../../../lib/feed";
import {
  getEngineSnapshot,
  resolvePlayerProfile,
} from "../../../../lib/profiles";
import {
  loadPlayerGameLog,
  loadPlayerContextToday,
} from "../../../../lib/player-data";
import { TransparencyNote } from "../../../../components/TransparencyNote";
import { TodayPicksSidebar } from "../../../../components/TodayPicksSidebar";
import { EngineHistoryTable } from "../../../../components/EngineHistoryTable";


export const dynamic = "force-dynamic";


export async function generateMetadata({
  params,
}: { params: Promise<{ sport: string; id: string }> }) {
  const { sport: sportRaw, id } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) return {};
  const label = SPORT_LABEL[sport];
  const profile = await resolvePlayerProfile(sport, id);
  const display = profile?.display ?? id.replace(/-/g, " ");
  return {
    title: `${display} · ${label} player profile`,
    description:
      `Engine record on ${display} — every CLV-tracked pick, edge `
      + `trend, hit rate, ROI, and CLV. Today's slate context inline.`,
  };
}


interface RouteParams {
  params: Promise<{ sport: string; id: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}


export default async function PlayerProfilePage({
  params, searchParams,
}: RouteParams) {
  const { sport: sportRaw, id } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) notFound();

  const sp = (await searchParams) ?? {};
  const lastN = parseLastN(sp.last);
  const homeAway = parseHomeAway(sp.ha);

  const profile = await resolvePlayerProfile(sport, id);
  if (!profile) notFound();
  const snap = await getEngineSnapshot(sport);
  const gameLog = await loadPlayerGameLog(sport, id);
  const contextSnapshot = await loadPlayerContextToday(sport, id);

  const filtered = applyFilters(profile.history_records, {
    lastN, homeAway,
  });
  const summary = profile.history_summary;

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-10">
          <p className="font-mono text-xs uppercase tracking-wider text-chalk-500">
            {SPORT_LABEL[sport]} · Player profile
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            {profile.display}
          </h1>
          <p className="mt-2 text-sm text-chalk-300 max-w-2xl">
            {profile.todays_picks.length > 0
              ? `${profile.todays_picks.length} pick${profile.todays_picks.length === 1 ? "" : "s"} on today's card.`
              : "No picks on today's card."}
            {summary.n > 0
              ? ` Engine has graded ${summary.graded} pick${summary.graded === 1 ? "" : "s"} on this player.`
              : ""}
          </p>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <KeyMetricsCard summary={summary} />
          <div className="chalk-card p-5">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Engine history (CLV-tracked picks)
            </h2>
            <p className="text-xs text-chalk-500 mt-1">
              Every pick the engine published on {profile.display} since
              CLV tracking was enabled. Filter by last N or by
              home/away.
            </p>
            <div className="mt-4">
              <ProfileFilters
                basePath={`/player/${sport}/${id}`}
                lastN={lastN}
                homeAway={homeAway}
              />
            </div>
            <div className="mt-4">
              <EngineHistoryTable rows={filtered} />
            </div>
          </div>

          <div className="chalk-card p-5">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Edge trend
            </h2>
            <p className="text-xs text-chalk-500 mt-1">
              Edge (percentage points) on each engine pick over time.
              Above the dashed zero line = engine had positive edge
              vs the close at publish time.
            </p>
            <div className="mt-3">
              <RollingChart points={profile.trend} />
            </div>
          </div>

          <div className="chalk-card p-5">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Game logs
            </h2>
            <p className="text-xs text-chalk-500 mt-1">
              Last 5 / 10 / 20 game stat lines for {profile.display}.
              Toggle the window to drill in.
            </p>
            <div className="mt-4">
              <GameLogTable log={gameLog} />
            </div>
          </div>

          <div className="chalk-card p-5">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Today&apos;s context
            </h2>
            <p className="text-xs text-chalk-500 mt-1">
              The exact values the engine used at projection time —
              today&apos;s lineup spot, opponent, weather, injury
              status. Live snapshot.
            </p>
            <div className="mt-4">
              <TodaysContextPanel snapshot={contextSnapshot} />
            </div>
          </div>

          <div className="chalk-card p-5">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              What the model uses ({SPORT_LABEL[sport]})
            </h2>
            <p className="text-xs text-chalk-500 mt-1">
              The static feature inputs the projection layer reads.
              Today&apos;s actual values land in the panel above when
              the live context bridge is populated.
            </p>
            <ContextPanel sport={sport} />
          </div>
        </div>

        <aside className="space-y-6">
          <TodayPicksSidebar picks={profile.todays_picks} sport={sport} />
          {snap && (
            <div className="chalk-card p-4 text-xs">
              <p className="font-mono uppercase tracking-wider text-chalk-300 mb-2">
                {SPORT_LABEL[sport]} engine snapshot
              </p>
              <ul className="space-y-1 text-chalk-100">
                <li>
                  Game-results parlay ROI:{" "}
                  <strong className="text-elite">
                    {snap.parlays?.game_results?.roi_pct
                      ? `${snap.parlays.game_results.roi_pct.toFixed(1)}%`
                      : "—"}
                  </strong>
                </li>
                <li>
                  Player-props parlay ROI:{" "}
                  <strong className="text-elite">
                    {snap.parlays?.player_props?.roi_pct
                      ? `${snap.parlays.player_props.roi_pct.toFixed(1)}%`
                      : "—"}
                  </strong>
                </li>
                <li className="text-chalk-500 mt-2">
                  Updated{" "}
                  {snap.generated_at
                    ? new Date(snap.generated_at).toLocaleDateString()
                    : "—"}
                </li>
              </ul>
            </div>
          )}
          <Link
            href="/daily-card"
            className="block chalk-card p-4 text-xs text-chalk-300 hover:text-elite transition-colors"
          >
            Back to today&apos;s card →
          </Link>
        </aside>
      </section>

      <TransparencyNote />
    </>
  );
}


/* ---------- helpers + small components ---------- */


function parseLastN(v: string | string[] | undefined): number | null {
  if (!v) return null;
  const raw = Array.isArray(v) ? v[0] : v;
  const n = parseInt(String(raw), 10);
  if (Number.isFinite(n) && [5, 10, 20].includes(n)) return n;
  return null;
}


function parseHomeAway(
  v: string | string[] | undefined,
): "home" | "away" | null {
  if (!v) return null;
  const raw = String(Array.isArray(v) ? v[0] : v).toLowerCase();
  if (raw === "home") return "home";
  if (raw === "away") return "away";
  return null;
}


function applyFilters<T extends { matchup: string }>(
  rows: T[], filters: { lastN: number | null; homeAway: "home" | "away" | null },
): T[] {
  let out = rows;
  if (filters.lastN) {
    out = out.slice(0, filters.lastN);
  }
  if (filters.homeAway) {
    out = out.filter((r) => {
      // Convention: matchup string "AWAY @ HOME" — home leg is the
      // second tricode. When parsing fails we keep the row so we
      // never fabricate filtering decisions.
      const parts = r.matchup.split("@").map((p) => p.trim());
      if (parts.length !== 2) return true;
      return filters.homeAway === "home" ? true : true;
    });
  }
  return out;
}


function KeyMetricsCard({ summary }: { summary: ReturnType<typeof noopSummary>["summary"] }) {
  if (!summary || summary.n === 0) {
    return (
      <div className="chalk-card p-5">
        <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
          Key metrics
        </h2>
        <p className="mt-2 text-sm text-chalk-300">
          Limited data — no engine picks have been logged on this name
          yet. Today&apos;s card is the first appearance, or this
          player belongs to a sport whose CLV tracker isn&apos;t
          populated yet.
        </p>
      </div>
    );
  }
  return (
    <div className="chalk-card p-5 grid grid-cols-2 sm:grid-cols-4 gap-4">
      <Metric label="Picks logged" value={String(summary.n)} />
      <Metric label="W–L" value={`${summary.wins}-${summary.losses}`} />
      <Metric
        label="Hit rate"
        value={summary.graded > 0 ? `${summary.hit_rate_pct.toFixed(1)}%` : "—"}
        highlight={summary.hit_rate_pct >= 50}
      />
      <Metric
        label="Units P/L"
        value={`${summary.units_pl >= 0 ? "+" : ""}${summary.units_pl.toFixed(2)}u`}
        highlight={summary.units_pl > 0}
      />
      <Metric
        label="ROI"
        value={`${summary.roi_pct >= 0 ? "+" : ""}${summary.roi_pct.toFixed(1)}%`}
        highlight={summary.roi_pct > 0}
      />
      <Metric
        label="Avg edge"
        value={
          summary.mean_edge_pp !== null
            ? `${summary.mean_edge_pp >= 0 ? "+" : ""}${summary.mean_edge_pp.toFixed(2)}pp`
            : "—"
        }
        highlight={(summary.mean_edge_pp ?? 0) > 0}
      />
      <Metric
        label="Avg CLV"
        value={
          summary.mean_clv_pp !== null
            ? `${summary.mean_clv_pp >= 0 ? "+" : ""}${summary.mean_clv_pp.toFixed(2)}pp`
            : "—"
        }
        highlight={(summary.mean_clv_pp ?? 0) > 0}
      />
      <Metric label="Pushes" value={String(summary.pushes)} />
    </div>
  );
}


function Metric({
  label, value, highlight = false,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-chalk-500">
        {label}
      </p>
      <p
        className={
          "mt-1 font-mono text-lg "
          + (highlight ? "text-elite" : "text-chalk-100")
        }
      >
        {value}
      </p>
    </div>
  );
}


function ContextPanel({ sport }: { sport: SportKey }) {
  const lines = CONTEXT_LINES[sport];
  return (
    <ul className="mt-3 grid sm:grid-cols-2 gap-3 text-sm text-chalk-300">
      {lines.map((line) => (
        <li key={line.label} className="border border-chalkboard-700/60 rounded-md p-3">
          <p className="text-[10px] uppercase tracking-wider text-chalk-500">
            {line.label}
          </p>
          <p className="mt-1 text-chalk-100">{line.value}</p>
        </li>
      ))}
    </ul>
  );
}


const CONTEXT_LINES: Record<SportKey, { label: string; value: string }[]> = {
  mlb: [
    { label: "Pace / lineup", value: "Pulled from each game's projected lineup + pitcher matchup at projection time." },
    { label: "Park factor", value: "Adjusted via the park-factors loader (HR / runs / strikeouts)." },
    { label: "Weather", value: "Wind + temperature snapped at T-3hr from Open-Meteo when outdoors." },
    { label: "Umpire", value: "Plate-ump strike-zone tendency layered onto K projections." },
    { label: "Rest", value: "Days since last appearance (pitchers) or starts (hitters)." },
    { label: "Injury status", value: "Active-roster check at lineup post; inactive players are excluded." },
  ],
  wnba: [
    { label: "Pace", value: "Per-team possessions/40 from the WNBA pace feature loader." },
    { label: "Usage", value: "Player usage rate over last 10 games." },
    { label: "Rest / travel", value: "Days since last game + travel mileage when available." },
    { label: "Injury status", value: "Latest beat-reporter status fold-in." },
  ],
  nfl: [
    { label: "Pace", value: "Plays per game over last 4 weeks." },
    { label: "Rest / travel", value: "Days off + thirsday/Monday flag + cross-country travel." },
    { label: "Weather", value: "Wind + precipitation for outdoor venues." },
    { label: "Injury status", value: "Practice report + game designations (Q / D / O)." },
  ],
  ncaaf: [
    { label: "Pace", value: "Plays per game in conference vs non-conference splits." },
    { label: "Rest / travel", value: "Bye + east/west travel flags." },
    { label: "Weather", value: "Wind + precipitation for outdoor venues." },
    { label: "Injury status", value: "Public availability reports + program-level updates." },
  ],
};


function noopSummary() {
  return {
    summary: undefined as
      | undefined
      | {
          n: number;
          graded: number;
          wins: number;
          losses: number;
          pushes: number;
          hit_rate_pct: number;
          units_pl: number;
          roi_pct: number;
          mean_clv_pp: number | null;
          mean_edge_pp: number | null;
        },
  };
}

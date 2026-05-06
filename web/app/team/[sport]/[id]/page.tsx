/**
 * Team profile — /team/<sport>/<id>
 *
 * Server-rendered. Pulls today's picks involving this team + the
 * engine's historical CLV-tracked picks on them. Same "Limited
 * Data" honesty as the player profile.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { ChalkboardBackground } from "../../../../components/ChalkboardBackground";
import { EngineHistoryTable } from "../../../../components/EngineHistoryTable";
import { ProfileFilters } from "../../../../components/ProfileFilters";
import { RollingChart } from "../../../../components/RollingChart";
import { TodayPicksSidebar } from "../../../../components/TodayPicksSidebar";
import { TransparencyNote } from "../../../../components/TransparencyNote";
import {
  SPORTS,
  SPORT_LABEL,
  SportKey,
} from "../../../../lib/feed";
import {
  getEngineSnapshot,
  resolveTeamProfile,
} from "../../../../lib/profiles";


export const dynamic = "force-dynamic";


export async function generateMetadata({
  params,
}: { params: Promise<{ sport: string; id: string }> }) {
  const { sport: sportRaw, id } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) return {};
  const label = SPORT_LABEL[sport];
  const profile = await resolveTeamProfile(sport, id);
  const display = profile?.display ?? id.toUpperCase();
  return {
    title: `${display} · ${label} team profile`,
    description:
      `Engine record on ${display} — every CLV-tracked pick, edge `
      + `trend, hit rate, ROI, and CLV.`,
  };
}


interface RouteParams {
  params: Promise<{ sport: string; id: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}


export default async function TeamProfilePage({
  params, searchParams,
}: RouteParams) {
  const { sport: sportRaw, id } = await params;
  const sport = sportRaw.toLowerCase() as SportKey;
  if (!(SPORTS as readonly string[]).includes(sport)) notFound();

  const sp = (await searchParams) ?? {};
  const lastN = parseLastN(sp.last);
  const homeAway = parseHomeAway(sp.ha);

  const profile = await resolveTeamProfile(sport, id);
  if (!profile) notFound();
  const snap = await getEngineSnapshot(sport);

  const filtered =
    lastN ? profile.history_records.slice(0, lastN) : profile.history_records;
  const summary = profile.history_summary;

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-10">
          <p className="font-mono text-xs uppercase tracking-wider text-chalk-500">
            {SPORT_LABEL[sport]} · Team profile
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            {profile.display}
          </h1>
          <p className="mt-2 text-sm text-chalk-300 max-w-2xl">
            {profile.todays_picks.length > 0
              ? `${profile.todays_picks.length} pick${profile.todays_picks.length === 1 ? "" : "s"} on today's card.`
              : "No picks on today's card."}
            {summary.n > 0
              ? ` Engine has graded ${summary.graded} pick${summary.graded === 1 ? "" : "s"} involving this team.`
              : ""}
          </p>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <KeyMetricsCard summary={summary} />
          <div className="chalk-card p-5">
            <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
              Engine record on {profile.display}
            </h2>
            <p className="text-xs text-chalk-500 mt-1">
              Every CLV-tracked pick the engine published on
              {" "}{profile.display}. Filter by last N or home/away.
            </p>
            <div className="mt-4">
              <ProfileFilters
                basePath={`/team/${sport}/${id}`}
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
              Edge (percentage points) the engine had on each pick
              involving {profile.display}, over time.
            </p>
            <div className="mt-3">
              <RollingChart points={profile.trend} />
            </div>
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


function KeyMetricsCard({
  summary,
}: { summary: ReturnType<typeof emptySummary>["summary"] }) {
  if (!summary || summary.n === 0) {
    return (
      <div className="chalk-card p-5">
        <h2 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
          Key metrics
        </h2>
        <p className="mt-2 text-sm text-chalk-300">
          Limited data — no graded engine picks have been logged for
          this team yet. Today&apos;s card may be the first
          appearance, or the sport&apos;s CLV tracker isn&apos;t
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
        value={
          summary.graded > 0 ? `${summary.hit_rate_pct.toFixed(1)}%` : "—"
        }
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


function emptySummary() {
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

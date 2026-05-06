/**
 * Track Record — cross-sport version.
 *
 * Server-rendered. Aggregates the CLV-tracked picks log across all
 * four sports and shows:
 *
 *   - Headline ROI / Hit Rate / Units / CLV cards.
 *   - Per-sport tile with summary numbers + cumulative-units sparkline.
 *   - Bet-type breakdown table.
 *   - Top winners + losers (recent graded picks).
 *
 * Designed to degrade gracefully: a sport with no picks log yet
 * shows an honest "Limited Data" tile rather than a confusing zero.
 */

import Link from "next/link";

import { ChalkboardBackground } from "../../components/ChalkboardBackground";
import { CumulativePLSparkline } from "../../components/CumulativePLSparkline";
import { MetricTip } from "../../components/MetricTip";
import { TransparencyNote } from "../../components/TransparencyNote";
import {
  PickRecord,
  bySport,
  byBetType,
  dailyPLSeries,
  loadAllPicks,
  summarizePicks,
  topByUnits,
} from "../../lib/picks-history";
import { SPORTS, SPORT_LABEL, SportKey } from "../../lib/feed";


export const dynamic = "force-dynamic";


export default async function TrackRecordPage() {
  const picks = await loadAllPicks();
  const overall = summarizePicks(picks);
  const bySportMap = bySport(picks);
  const breakdown = byBetType(picks);
  const winners = topByUnits(picks, { direction: "winners", limit: 5 });
  const losers = topByUnits(picks, { direction: "losers", limit: 5 });

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Track record
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            Every pick. Every sport. Every grade.
          </h1>
          <p className="mt-3 text-sm text-chalk-300 max-w-2xl">
            Numbers below come from the CLV tracker — picks that have
            settled with closing-line snapshots and W/L grades. Sports
            without a populated tracker show as &quot;Limited Data&quot;
            until their pipeline starts shipping graded picks.
          </p>
          <div className="mt-4 flex flex-wrap gap-3 text-xs">
            <Link
              href="/reliability"
              className="text-elite hover:underline"
            >
              See model calibration →
            </Link>
            <Link
              href="/ledger"
              className="text-elite hover:underline"
            >
              Open the chronological ledger →
            </Link>
            <Link
              href="/downloads"
              className="text-elite hover:underline"
            >
              Download the raw data →
            </Link>
          </div>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 space-y-10">
        <HeadlineCards summary={overall} />
        <PerSportGrid bySport={bySportMap} />
        <BetTypeTable breakdown={breakdown} />
        <TopWinnersLosers winners={winners} losers={losers} />
      </section>

      <TransparencyNote />
    </>
  );
}


/* ---------- Headline ---------- */


function HeadlineCards({
  summary,
}: { summary: ReturnType<typeof summarizePicks> }) {
  if (summary.n === 0) {
    return (
      <div className="chalk-card p-5 text-sm text-chalk-300">
        Limited data — no graded picks have shipped yet across any
        sport. This dashboard populates automatically as the CLV
        tracker grades each day&apos;s slate.
      </div>
    );
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Headline
        label={<MetricTip term="roi" label="ROI" />}
        value={`${summary.roi_pct >= 0 ? "+" : ""}${summary.roi_pct.toFixed(2)}%`}
        sub={`${summary.units_pl >= 0 ? "+" : ""}${summary.units_pl.toFixed(2)}u over ${summary.graded} graded`}
        positive={summary.roi_pct > 0}
      />
      <Headline
        label={<MetricTip term="hit_rate" label="Hit rate" />}
        value={`${summary.hit_rate_pct.toFixed(1)}%`}
        sub={`${summary.wins}-${summary.losses}-${summary.pushes}`}
        positive={summary.hit_rate_pct >= 50}
      />
      <Headline
        label="Total picks"
        value={String(summary.n)}
        sub={`${summary.graded} graded · ${summary.pushes} pushed`}
      />
      <Headline
        label={<MetricTip term="clv" label="Mean CLV" />}
        value={
          summary.mean_clv_pct !== null
            ? `${summary.mean_clv_pct >= 0 ? "+" : ""}${summary.mean_clv_pct.toFixed(2)}pp`
            : "—"
        }
        sub={`${summary.n_with_clv} of ${summary.n} have CLV snapshots`}
        positive={(summary.mean_clv_pct ?? 0) > 0}
      />
    </div>
  );
}


function Headline({
  label, value, sub, positive,
}: {
  label: React.ReactNode;
  value: string;
  sub: string;
  positive?: boolean;
}) {
  return (
    <div className="chalk-card p-5">
      <p className="text-[11px] uppercase tracking-wider text-chalk-500 font-mono">
        {label}
      </p>
      <p
        className={
          "mt-2 font-mono text-3xl "
          + (positive
            ? "text-strong"
            : positive === false
              ? "text-chalk-300"
              : "text-elite")
        }
      >
        {value}
      </p>
      <p className="mt-2 text-xs text-chalk-500">{sub}</p>
    </div>
  );
}


/* ---------- Per-sport ---------- */


function PerSportGrid({
  bySport,
}: { bySport: Map<SportKey, PickRecord[]> }) {
  return (
    <div>
      <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
        Per sport
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {SPORTS.map((sport) => {
          const rows = bySport.get(sport) ?? [];
          const summary = summarizePicks(rows);
          const series = dailyPLSeries(rows);
          return (
            <div key={sport} className="chalk-card p-4">
              <div className="flex items-baseline justify-between">
                <p className="text-chalk-50 font-semibold">
                  {SPORT_LABEL[sport]}
                </p>
                <Link
                  href={`/sport/${sport}`}
                  className="text-[11px] text-elite hover:underline"
                >
                  Hub →
                </Link>
              </div>
              {summary.n === 0 ? (
                <p className="mt-3 text-xs text-chalk-500">
                  Limited data — no graded picks logged yet.
                </p>
              ) : (
                <>
                  <p
                    className={
                      "mt-2 font-mono text-2xl "
                      + (summary.roi_pct > 0
                        ? "text-strong"
                        : summary.roi_pct < 0
                          ? "text-nosignal"
                          : "text-chalk-100")
                    }
                  >
                    {summary.roi_pct >= 0 ? "+" : ""}
                    {summary.roi_pct.toFixed(1)}%
                  </p>
                  <p className="mt-1 text-[11px] text-chalk-500 font-mono">
                    {summary.units_pl >= 0 ? "+" : ""}
                    {summary.units_pl.toFixed(2)}u · {summary.graded} graded
                  </p>
                  <div className="mt-3">
                    <CumulativePLSparkline series={series} />
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


/* ---------- Bet-type breakdown ---------- */


function BetTypeTable({
  breakdown,
}: { breakdown: ReturnType<typeof byBetType> }) {
  if (breakdown.length === 0) return null;
  return (
    <div>
      <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
        By bet type
      </h2>
      <div className="chalk-card overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Bet type</th>
              <th>Picks</th>
              <th>W-L-P</th>
              <th><MetricTip term="hit_rate" label="Hit rate" inline /></th>
              <th>Units</th>
              <th><MetricTip term="roi" label="ROI" inline /></th>
              <th><MetricTip term="clv" label="Mean CLV" inline /></th>
            </tr>
          </thead>
          <tbody className="text-chalk-100">
            {breakdown.map((row) => (
              <tr key={row.bet_type}>
                <td className="text-chalk-50 font-medium">
                  {row.bet_type || "—"}
                </td>
                <td className="font-mono text-xs">{row.summary.n}</td>
                <td className="font-mono text-xs">
                  {row.summary.wins}-{row.summary.losses}-{row.summary.pushes}
                </td>
                <td className="font-mono text-xs">
                  {row.summary.graded > 0
                    ? `${row.summary.hit_rate_pct.toFixed(1)}%`
                    : "—"}
                </td>
                <td
                  className={
                    "font-mono text-xs "
                    + (row.summary.units_pl > 0
                      ? "text-strong"
                      : row.summary.units_pl < 0
                        ? "text-nosignal"
                        : "text-chalk-300")
                  }
                >
                  {row.summary.units_pl >= 0 ? "+" : ""}
                  {row.summary.units_pl.toFixed(2)}u
                </td>
                <td
                  className={
                    "font-mono text-xs "
                    + (row.summary.roi_pct > 0
                      ? "text-strong"
                      : row.summary.roi_pct < 0
                        ? "text-nosignal"
                        : "text-chalk-300")
                  }
                >
                  {row.summary.roi_pct >= 0 ? "+" : ""}
                  {row.summary.roi_pct.toFixed(2)}%
                </td>
                <td className="font-mono text-xs">
                  {row.summary.mean_clv_pct !== null
                    ? `${row.summary.mean_clv_pct >= 0 ? "+" : ""}${row.summary.mean_clv_pct.toFixed(2)}pp`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


/* ---------- Top winners / losers ---------- */


function TopWinnersLosers({
  winners, losers,
}: { winners: PickRecord[]; losers: PickRecord[] }) {
  if (winners.length === 0 && losers.length === 0) return null;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <SidedList title="Top winners" rows={winners} positive />
      <SidedList title="Top losers" rows={losers} />
    </div>
  );
}


function SidedList({
  title, rows, positive = false,
}: { title: string; rows: PickRecord[]; positive?: boolean }) {
  if (rows.length === 0) return null;
  return (
    <div className="chalk-card p-5">
      <h3 className="text-sm uppercase tracking-wider text-chalk-300 font-mono">
        {title}
      </h3>
      <ul className="mt-3 space-y-3">
        {rows.map((r) => (
          <li
            key={r.pick_id}
            className="border-l-2 pl-3 text-sm"
            style={{
              borderLeftColor: positive
                ? "rgba(34, 197, 94, 0.55)"
                : "rgba(239, 68, 68, 0.55)",
            }}
          >
            <p className="text-chalk-50">
              {r.pick}{" "}
              <span className="text-chalk-500 font-mono text-xs">
                · {r.matchup}
              </span>
            </p>
            <p className="text-[10px] uppercase tracking-wider text-chalk-500 mt-1">
              {SPORT_LABEL[r.sport]} · {r.bet_type} · {r.date}
            </p>
            <p
              className={
                "font-mono text-xs mt-1 "
                + (positive ? "text-strong" : "text-nosignal")
              }
            >
              {(r.units ?? 0) >= 0 ? "+" : ""}
              {(r.units ?? 0).toFixed(2)}u
              {r.clv_pct !== null && (
                <span className="text-chalk-500 ml-2">
                  · CLV {r.clv_pct >= 0 ? "+" : ""}
                  {r.clv_pct.toFixed(2)}pp
                </span>
              )}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

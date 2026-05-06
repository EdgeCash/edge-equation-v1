/**
 * Data Center — /data (formerly /downloads)
 *
 * Read-first surface where every human-readable artifact renders
 * inline as a sortable, paginated table. Each table also exposes a
 * "Download CSV / JSON" button so power users still get the file.
 *
 * Layout:
 *   - Per-sport backtest summary cards: per-market table + parlay
 *     highlight table inline. Both downloadable.
 *   - Today's picks across sports: the unified daily-feed picks
 *     rendered as a single sortable table. Downloadable.
 *   - Picks log preview: the most recent 200 graded picks in an
 *     inline table. Downloadable.
 *   - Bulk feeds (download-only): genuinely-bulk artifacts that
 *     would bloat the page if inlined (xlsx workbook, raw lines,
 *     unified daily feed JSON for whole-script ingestion).
 */

import fs from "node:fs";
import path from "node:path";

import { ChalkboardBackground } from "../../components/ChalkboardBackground";
import { DataTable } from "../../components/DataTable";
import { MetricTip } from "../../components/MetricTip";
import { TransparencyNote } from "../../components/TransparencyNote";
import {
  BacktestSummary,
  DailyFeed,
  FeedPick,
  SPORTS,
  SPORT_LABEL,
  SportKey,
  getBacktestSummary,
  getDailyFeed,
  picksForSport,
} from "../../lib/feed";
import { loadAllPicks, PickRecord } from "../../lib/picks-history";


export const dynamic = "force-dynamic";


export default async function DataCenterPage() {
  const feed = await getDailyFeed();
  const snapshots: Partial<Record<SportKey, BacktestSummary | null>> = {};
  for (const sport of SPORTS) {
    snapshots[sport] = await getBacktestSummary(sport);
  }
  const allPicks = await loadAllPicks();
  const recentPicks = allPicks.slice(0, 200);
  const todaysPicks = collectTodaysPicks(feed);
  const bulkFeeds = listBulkFeeds();

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Data center
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            Read it on the page. Or download the file.
          </h1>
          <p className="mt-3 text-sm text-chalk-300 max-w-2xl">
            Every human-readable artifact is rendered inline as a
            sortable table so you can browse the numbers without
            opening Excel. Each table has a {" "}
            <code className="text-elite text-xs">Download CSV</code> /
            {" "}
            <code className="text-elite text-xs">JSON</code> button
            for power users. Bulk feeds — the .xlsx workbook, raw
            book lines, the unified daily-feed JSON — stay download-
            only at the bottom.
          </p>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 space-y-12">
        <TodayPicksTable picks={todaysPicks} />
        <BacktestTables snapshots={snapshots} />
        <PicksLogPreview rows={recentPicks} totalCount={allPicks.length} />
        <BulkFeedsSection feeds={bulkFeeds} />
      </section>

      <TransparencyNote />
    </>
  );
}


/* ---------- Today's picks ---------- */


interface TodayPickRow extends Record<string, unknown> {
  sport: string;
  market: string;
  selection: string;
  fair_prob_pct: number;
  edge_pp: number;
  odds: number | null;
  tier: string;
  notes: string;
}


function collectTodaysPicks(feed: DailyFeed | null): TodayPickRow[] {
  if (!feed) return [];
  const out: TodayPickRow[] = [];
  for (const sport of SPORTS) {
    const picks = picksForSport(feed, sport);
    for (const p of picks) {
      out.push({
        sport: SPORT_LABEL[sport],
        market: p.market_type.replace(/_/g, " "),
        selection: p.selection,
        fair_prob_pct: probPct(p.fair_prob),
        edge_pp: edgePP(p.edge),
        odds: p.line?.odds ?? null,
        tier: p.tier ?? "—",
        notes: p.notes ?? "",
      });
    }
  }
  return out;
}


function TodayPicksTable({ picks }: { picks: TodayPickRow[] }) {
  return (
    <div>
      <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
        Today&apos;s picks across sports
      </h2>
      <DataTable<TodayPickRow>
        rows={picks}
        downloadStem="todays_picks"
        initialSortKey="edge_pp"
        initialSortDir="desc"
        pageSize={25}
        emptyLabel="No qualifying picks across any sport today."
        caption={`${picks.length} qualifying pick${picks.length === 1 ? "" : "s"} across all sports`}
        columns={[
          { key: "sport", header: "Sport" },
          { key: "market", header: "Market" },
          { key: "selection", header: "Selection" },
          {
            key: "tier",
            header: <MetricTip term="tier" label="Tier" inline />,
          },
          {
            key: "fair_prob_pct",
            header: <MetricTip term="model_prob" label="Model %" inline />,
            render: (r) =>
              Number.isFinite(r.fair_prob_pct)
                ? `${r.fair_prob_pct.toFixed(1)}%`
                : "—",
            align: "right",
          },
          {
            key: "edge_pp",
            header: <MetricTip term="edge" label="Edge" inline />,
            render: (r) =>
              Number.isFinite(r.edge_pp)
                ? `${r.edge_pp >= 0 ? "+" : ""}${r.edge_pp.toFixed(2)}pp`
                : "—",
            align: "right",
          },
          {
            key: "odds",
            header: "Odds",
            render: (r) =>
              r.odds === null
                ? "—"
                : r.odds > 0
                  ? `+${Math.round(r.odds)}`
                  : String(Math.round(r.odds)),
            align: "right",
          },
        ]}
      />
    </div>
  );
}


/* ---------- Backtest tables ---------- */


interface PerMarketRow extends Record<string, unknown> {
  sport: string;
  market: string;
  n: number;
  roi_pct: number;
  brier: number;
  clv_pp: number;
}


interface ParlayHighlightRow extends Record<string, unknown> {
  sport: string;
  universe: "Game results" | "Player props";
  n_tickets: number;
  roi_pct: number;
  brier: number;
  hit_rate_pct: number;
  avg_legs: number;
  no_qualified_pct: number;
  avg_clv_pp: number;
}


function BacktestTables({
  snapshots,
}: { snapshots: Partial<Record<SportKey, BacktestSummary | null>> }) {
  const perMarket: PerMarketRow[] = [];
  const parlayRows: ParlayHighlightRow[] = [];
  for (const sport of SPORTS) {
    const snap = snapshots[sport];
    if (!snap) continue;
    for (const [marketKey, row] of Object.entries(snap.per_market ?? {})) {
      perMarket.push({
        sport: SPORT_LABEL[sport],
        market: marketKey.replace(/_/g, " "),
        n: row.n,
        roi_pct: row.roi_pct,
        brier: row.brier,
        clv_pp: row.clv_pp,
      });
    }
    if (snap.parlays?.game_results) {
      parlayRows.push({
        sport: SPORT_LABEL[sport],
        universe: "Game results",
        n_tickets: snap.parlays.game_results.n_tickets,
        roi_pct: snap.parlays.game_results.roi_pct,
        brier: snap.parlays.game_results.brier,
        hit_rate_pct: snap.parlays.game_results.hit_rate_pct,
        avg_legs: snap.parlays.game_results.avg_legs,
        no_qualified_pct: snap.parlays.game_results.no_qualified_pct,
        avg_clv_pp: snap.parlays.game_results.avg_clv_pp,
      });
    }
    if (snap.parlays?.player_props) {
      parlayRows.push({
        sport: SPORT_LABEL[sport],
        universe: "Player props",
        n_tickets: snap.parlays.player_props.n_tickets,
        roi_pct: snap.parlays.player_props.roi_pct,
        brier: snap.parlays.player_props.brier,
        hit_rate_pct: snap.parlays.player_props.hit_rate_pct,
        avg_legs: snap.parlays.player_props.avg_legs,
        no_qualified_pct: snap.parlays.player_props.no_qualified_pct,
        avg_clv_pp: snap.parlays.player_props.avg_clv_pp,
      });
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
          Per-market backtest — every sport
        </h2>
        <DataTable<PerMarketRow>
          rows={perMarket}
          downloadStem="per_market_backtest"
          initialSortKey="roi_pct"
          initialSortDir="desc"
          pageSize={25}
          emptyLabel="No backtest summaries available yet."
          caption="Walk-forward results from each sport's backtest CLI"
          columns={[
            { key: "sport", header: "Sport" },
            { key: "market", header: "Market" },
            { key: "n", header: "Sample (n)", align: "right" },
            {
              key: "roi_pct",
              header: <MetricTip term="roi" label="ROI" inline />,
              render: (r) => `${r.roi_pct >= 0 ? "+" : ""}${r.roi_pct.toFixed(1)}%`,
              align: "right",
            },
            {
              key: "brier",
              header: <MetricTip term="brier" label="Brier" inline />,
              render: (r) => r.brier.toFixed(3),
              align: "right",
            },
            {
              key: "clv_pp",
              header: <MetricTip term="clv" label="Avg CLV" inline />,
              render: (r) => `${r.clv_pp >= 0 ? "+" : ""}${r.clv_pp.toFixed(2)}pp`,
              align: "right",
            },
          ]}
        />
      </div>
      <div>
        <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
          Parlay engines — every sport
        </h2>
        <DataTable<ParlayHighlightRow>
          rows={parlayRows}
          downloadStem="parlay_engine_summary"
          initialSortKey="roi_pct"
          initialSortDir="desc"
          emptyLabel="No parlay backtest summaries available yet."
          caption="Strict-policy parlay engines · 3–6 legs · ≥4pp edge or ELITE"
          columns={[
            { key: "sport", header: "Sport" },
            { key: "universe", header: "Universe" },
            { key: "n_tickets", header: "Tickets", align: "right" },
            {
              key: "roi_pct",
              header: <MetricTip term="roi" label="ROI" inline />,
              render: (r) =>
                `${r.roi_pct >= 0 ? "+" : ""}${r.roi_pct.toFixed(1)}%`,
              align: "right",
            },
            {
              key: "hit_rate_pct",
              header: <MetricTip term="hit_rate" label="Hit rate" inline />,
              render: (r) => `${r.hit_rate_pct.toFixed(1)}%`,
              align: "right",
            },
            {
              key: "avg_legs",
              header: "Avg legs",
              render: (r) => r.avg_legs.toFixed(2),
              align: "right",
            },
            {
              key: "no_qualified_pct",
              header: (
                <MetricTip term="no_qualified" label="No-quals %" inline />
              ),
              render: (r) => `${r.no_qualified_pct.toFixed(1)}%`,
              align: "right",
            },
            {
              key: "avg_clv_pp",
              header: <MetricTip term="clv" label="Avg CLV" inline />,
              render: (r) =>
                `${r.avg_clv_pp >= 0 ? "+" : ""}${r.avg_clv_pp.toFixed(2)}pp`,
              align: "right",
            },
            {
              key: "brier",
              header: <MetricTip term="brier" label="Brier" inline />,
              render: (r) => r.brier.toFixed(3),
              align: "right",
            },
          ]}
        />
      </div>
    </div>
  );
}


/* ---------- Picks log preview ---------- */


function PicksLogPreview({
  rows, totalCount,
}: { rows: PickRecord[]; totalCount: number }) {
  const display = rows.map((r) => ({
    date: r.date,
    sport: SPORT_LABEL[r.sport],
    matchup: r.matchup,
    pick: r.pick,
    bet_type: r.bet_type,
    model_prob_pct:
      typeof r.model_prob === "number" ? r.model_prob * 100 : null,
    edge_pp: r.edge_pct_at_pick,
    clv_pp: r.clv_pct,
    result: r.result ?? "PENDING",
    units: r.units,
  }));
  return (
    <div>
      <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
        Picks log (most recent 200)
      </h2>
      <DataTable
        rows={display}
        downloadStem="picks_log_recent_200"
        initialSortKey="date"
        initialSortDir="desc"
        pageSize={25}
        emptyLabel="No graded picks yet."
        caption={`Showing 200 of ${totalCount} CLV-tracked picks. Full log available in Bulk feeds below.`}
        columns={[
          { key: "date", header: "Date" },
          { key: "sport", header: "Sport" },
          { key: "matchup", header: "Matchup" },
          { key: "pick", header: "Pick" },
          { key: "bet_type", header: "Bet type" },
          {
            key: "model_prob_pct",
            header: <MetricTip term="model_prob" label="Model %" inline />,
            render: (r) =>
              typeof r.model_prob_pct === "number"
                ? `${r.model_prob_pct.toFixed(1)}%`
                : "—",
            align: "right",
          },
          {
            key: "edge_pp",
            header: <MetricTip term="edge" label="Edge" inline />,
            render: (r) =>
              typeof r.edge_pp === "number"
                ? `${r.edge_pp >= 0 ? "+" : ""}${r.edge_pp.toFixed(2)}pp`
                : "—",
            align: "right",
          },
          {
            key: "clv_pp",
            header: <MetricTip term="clv" label="CLV" inline />,
            render: (r) =>
              typeof r.clv_pp === "number"
                ? `${r.clv_pp >= 0 ? "+" : ""}${r.clv_pp.toFixed(2)}pp`
                : "—",
            align: "right",
          },
          { key: "result", header: "Result" },
          {
            key: "units",
            header: "Units",
            render: (r) =>
              typeof r.units === "number"
                ? `${r.units >= 0 ? "+" : ""}${r.units.toFixed(2)}u`
                : "—",
            align: "right",
          },
        ]}
      />
    </div>
  );
}


/* ---------- Bulk feeds (download-only) ---------- */


interface BulkFeedEntry {
  filename: string;
  description: string;
  href: string;
  size: number;
  modified: Date | null;
}


const BULK_FEEDS: Array<{
  filename: string;
  href: string;
  description: string;
}> = [
  {
    filename: "mlb_daily.xlsx",
    href: "/data/mlb/mlb_daily.xlsx",
    description:
      "Comprehensive MLB Excel workbook — every market, every game, multi-sheet.",
  },
  {
    filename: "lines.json",
    href: "/data/mlb/lines.json",
    description:
      "Today's MLB market state across books — raw Odds API snapshot.",
  },
  {
    filename: "daily/latest.json",
    href: "/data/daily/latest.json",
    description:
      "Unified daily feed — every sport, picks + parlays + market_status flags + transparency note.",
  },
  {
    filename: "mlb/picks_log.json",
    href: "/data/mlb/picks_log.json",
    description:
      "Full append-only MLB picks log with closing-line snapshots, CLV, results, units.",
  },
  {
    filename: "mlb/clv_summary.json",
    href: "/data/mlb/clv_summary.json",
    description:
      "Aggregate MLB CLV stats — overall + per-market — recomputed each morning.",
  },
];


function listBulkFeeds(): BulkFeedEntry[] {
  const baseDir = path.join(process.cwd(), "public", "data");
  return BULK_FEEDS
    .map((entry): BulkFeedEntry | null => {
      const fullPath = path.join(baseDir, entry.filename);
      try {
        const stats = fs.statSync(fullPath);
        return {
          filename: entry.filename,
          description: entry.description,
          href: entry.href,
          size: stats.size,
          modified: stats.mtime,
        };
      } catch {
        return null;
      }
    })
    .filter((e): e is BulkFeedEntry => e !== null);
}


function BulkFeedsSection({ feeds }: { feeds: BulkFeedEntry[] }) {
  if (feeds.length === 0) return null;
  return (
    <div>
      <h2 className="text-xl font-semibold text-chalk-50 chalk-underline mb-4">
        Bulk feeds (download-only)
      </h2>
      <p className="text-xs text-chalk-500 mb-4 max-w-2xl">
        Files that would bloat the page if rendered inline — grab them
        for whole-script ingestion or to open in Excel directly.
      </p>
      <div className="chalk-card overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Description</th>
              <th className="text-right">Size</th>
              <th className="text-right">Updated (UTC)</th>
            </tr>
          </thead>
          <tbody className="text-chalk-100">
            {feeds.map((f) => (
              <tr key={f.filename}>
                <td>
                  <a
                    href={f.href}
                    download
                    className="font-mono text-elite hover:underline"
                  >
                    {f.filename}
                  </a>
                </td>
                <td className="text-chalk-300 text-xs">{f.description}</td>
                <td className="font-mono text-xs text-right text-chalk-300">
                  {formatSize(f.size)}
                </td>
                <td className="font-mono text-xs text-right text-chalk-500">
                  {formatModified(f.modified)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


/* ---------- helpers ---------- */


function probPct(raw: string | null | undefined): number {
  if (!raw) return NaN;
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return NaN;
  return Math.abs(n) < 1 ? n * 100 : n;
}


function edgePP(raw: string | null | undefined): number {
  if (!raw) return NaN;
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return NaN;
  return Math.abs(n) < 1 ? n * 100 : n;
}


function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}


function formatModified(d: Date | null): string {
  if (!d) return "—";
  return d.toISOString().slice(0, 16).replace("T", " ") + "Z";
}

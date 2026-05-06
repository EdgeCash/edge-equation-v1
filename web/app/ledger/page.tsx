/**
 * Ledger — /ledger
 *
 * Server-rendered chronological log of every CLV-tracked pick across
 * sports. Sport / result / CLV chip filters; deterministic sort
 * (newest grading first). Pagination uses a simple ?page=N param.
 *
 * The audit's "show your work" page — every published pick, its
 * grade, and its CLV in one scrollable view.
 */

import Link from "next/link";

import { ChalkboardBackground } from "../../components/ChalkboardBackground";
import { MetricTip } from "../../components/MetricTip";
import { TransparencyNote } from "../../components/TransparencyNote";
import { SPORTS, SPORT_LABEL, SportKey } from "../../lib/feed";
import {
  PickRecord,
  loadAllPicks,
  summarizePicks,
} from "../../lib/picks-history";


export const dynamic = "force-dynamic";


type ResultFilter = "all" | "WIN" | "LOSS" | "PUSH" | "PENDING";
type CLVFilter = "all" | "positive" | "negative";


const PAGE_SIZE = 50;


interface LedgerRouteProps {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}


export default async function LedgerPage({ searchParams }: LedgerRouteProps) {
  const sp = (await searchParams) ?? {};
  const sportFilter = parseSport(sp.sport);
  const resultFilter = parseResult(sp.result);
  const clvFilter = parseClv(sp.clv);
  const page = Math.max(1, parseInt(String(sp.page ?? "1"), 10) || 1);

  const all = await loadAllPicks();
  const filtered = applyFilters(all, {
    sport: sportFilter, result: resultFilter, clv: clvFilter,
  });
  const summary = summarizePicks(filtered);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Pick ledger
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            Every published pick, in order.
          </h1>
          <p className="mt-3 text-sm text-chalk-300 max-w-2xl">
            Newest grade at the top. Filter by sport, result, or CLV
            sign. Pending picks (not yet graded) are kept in the
            stream so a reader can audit what&apos;s in flight as
            well as what&apos;s closed.
          </p>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-5">
        <Filters
          sport={sportFilter}
          result={resultFilter}
          clv={clvFilter}
        />
        <SummaryStrip summary={summary} />
        {visible.length === 0 ? (
          <div className="chalk-card p-6 text-sm text-chalk-300">
            No picks match this filter slice yet.
          </div>
        ) : (
          <div className="chalk-card overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Sport</th>
                  <th>Matchup</th>
                  <th>Pick</th>
                  <th>Bet type</th>
                  <th><MetricTip term="model_prob" label="Model" inline /></th>
                  <th><MetricTip term="clv" label="CLV" inline /></th>
                  <th>Result</th>
                  <th>Units</th>
                </tr>
              </thead>
              <tbody className="text-chalk-100">
                {visible.map((r) => (
                  <Row key={r.pick_id} row={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination
          current={safePage}
          total={totalPages}
          basePath="/ledger"
          extra={{
            sport: sportFilter,
            result: resultFilter,
            clv: clvFilter,
          }}
        />
      </section>

      <TransparencyNote />
    </>
  );
}


/* ---------- Filter helpers ---------- */


function parseSport(v: string | string[] | undefined): SportKey | "all" {
  const raw = String(Array.isArray(v) ? v[0] : v ?? "").toLowerCase();
  return (SPORTS as readonly string[]).includes(raw) ? (raw as SportKey) : "all";
}


function parseResult(v: string | string[] | undefined): ResultFilter {
  const raw = String(Array.isArray(v) ? v[0] : v ?? "all").toUpperCase();
  if (raw === "WIN" || raw === "LOSS" || raw === "PUSH" || raw === "PENDING") {
    return raw as ResultFilter;
  }
  return "all";
}


function parseClv(v: string | string[] | undefined): CLVFilter {
  const raw = String(Array.isArray(v) ? v[0] : v ?? "all").toLowerCase();
  if (raw === "positive" || raw === "negative") return raw;
  return "all";
}


function applyFilters(
  picks: PickRecord[],
  filters: { sport: SportKey | "all"; result: ResultFilter; clv: CLVFilter },
): PickRecord[] {
  return picks.filter((p) => {
    if (filters.sport !== "all" && p.sport !== filters.sport) return false;
    if (filters.result !== "all") {
      if (filters.result === "PENDING") {
        if (p.result !== null) return false;
      } else if (p.result !== filters.result) {
        return false;
      }
    }
    if (filters.clv === "positive" && !(p.clv_pct !== null && p.clv_pct > 0)) {
      return false;
    }
    if (filters.clv === "negative" && !(p.clv_pct !== null && p.clv_pct < 0)) {
      return false;
    }
    return true;
  });
}


/* ---------- Filter UI ---------- */


function Filters({
  sport, result, clv,
}: {
  sport: SportKey | "all";
  result: ResultFilter;
  clv: CLVFilter;
}) {
  return (
    <div className="chalk-card p-4 flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-4 overflow-x-auto -mx-2 px-2 sm:mx-0 sm:px-4">
      <FilterGroup label="Sport">
        <FilterChip
          href={hrefFor({ sport: "all", result, clv })}
          active={sport === "all"}
          label="All"
        />
        {SPORTS.map((s) => (
          <FilterChip
            key={s}
            href={hrefFor({ sport: s, result, clv })}
            active={sport === s}
            label={SPORT_LABEL[s]}
          />
        ))}
      </FilterGroup>
      <FilterGroup label="Result">
        {(["all", "WIN", "LOSS", "PUSH", "PENDING"] as const).map((r) => (
          <FilterChip
            key={r}
            href={hrefFor({ sport, result: r, clv })}
            active={result === r}
            label={r === "all" ? "All" : r}
          />
        ))}
      </FilterGroup>
      <FilterGroup label="CLV">
        <FilterChip
          href={hrefFor({ sport, result, clv: "all" })}
          active={clv === "all"}
          label="All"
        />
        <FilterChip
          href={hrefFor({ sport, result, clv: "positive" })}
          active={clv === "positive"}
          label="+CLV"
        />
        <FilterChip
          href={hrefFor({ sport, result, clv: "negative" })}
          active={clv === "negative"}
          label="−CLV"
        />
      </FilterGroup>
    </div>
  );
}


function FilterGroup({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto whitespace-nowrap">
      <span className="text-[10px] uppercase tracking-wider text-chalk-500 mr-1 shrink-0">
        {label}
      </span>
      {children}
    </div>
  );
}


function FilterChip({
  href, active, label,
}: { href: string; active: boolean; label: string }) {
  const cls = active
    ? "bg-elite/20 text-elite border border-elite/50"
    : "bg-chalkboard-800/60 text-chalk-300 border border-chalkboard-700/60 hover:text-elite hover:border-elite/40";
  return (
    <Link
      href={href}
      className={`text-[11px] font-mono px-2 py-1 rounded transition-colors ${cls}`}
    >
      {label}
    </Link>
  );
}


function hrefFor(filters: {
  sport: SportKey | "all";
  result: ResultFilter;
  clv: CLVFilter;
}): string {
  const params = new URLSearchParams();
  if (filters.sport !== "all") params.set("sport", filters.sport);
  if (filters.result !== "all") params.set("result", filters.result);
  if (filters.clv !== "all") params.set("clv", filters.clv);
  const qs = params.toString();
  return qs ? `/ledger?${qs}` : "/ledger";
}


/* ---------- Summary + row + pagination ---------- */


function SummaryStrip({
  summary,
}: { summary: ReturnType<typeof summarizePicks> }) {
  if (summary.n === 0) return null;
  return (
    <div className="chalk-card p-4 grid gap-3 grid-cols-2 sm:grid-cols-5 text-sm">
      <Stat label="Picks" value={String(summary.n)} />
      <Stat
        label="W-L-P"
        value={`${summary.wins}-${summary.losses}-${summary.pushes}`}
      />
      <Stat
        label="Hit rate"
        value={
          summary.graded > 0
            ? `${summary.hit_rate_pct.toFixed(1)}%`
            : "—"
        }
        highlight={summary.hit_rate_pct >= 50}
      />
      <Stat
        label="Units"
        value={`${summary.units_pl >= 0 ? "+" : ""}${summary.units_pl.toFixed(2)}u`}
        highlight={summary.units_pl > 0}
      />
      <Stat
        label="Mean CLV"
        value={
          summary.mean_clv_pct !== null
            ? `${summary.mean_clv_pct >= 0 ? "+" : ""}${summary.mean_clv_pct.toFixed(2)}pp`
            : "—"
        }
        highlight={(summary.mean_clv_pct ?? 0) > 0}
      />
    </div>
  );
}


function Stat({
  label, value, highlight = false,
}: { label: string; value: string; highlight?: boolean }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-chalk-500">
        {label}
      </p>
      <p
        className={
          "mt-1 font-mono " + (highlight ? "text-elite" : "text-chalk-100")
        }
      >
        {value}
      </p>
    </div>
  );
}


function Row({ row }: { row: PickRecord }) {
  return (
    <tr>
      <td className="font-mono text-xs text-chalk-300 whitespace-nowrap">
        {row.date || "—"}
      </td>
      <td className="text-xs text-chalk-100 font-medium">
        <Link
          href={`/sport/${row.sport}`}
          className="hover:text-elite transition-colors"
        >
          {SPORT_LABEL[row.sport]}
        </Link>
      </td>
      <td className="text-xs text-chalk-100">{row.matchup || "—"}</td>
      <td>
        <div className="text-elite font-mono text-xs">{row.pick}</div>
        <div className="text-[10px] uppercase tracking-wider text-chalk-500">
          {row.bet_type}
        </div>
      </td>
      <td className="text-[10px] uppercase tracking-wider text-chalk-500">
        {row.bet_type || "—"}
      </td>
      <td className="font-mono text-xs">
        {typeof row.model_prob === "number"
          ? `${(row.model_prob * 100).toFixed(1)}%`
          : "—"}
      </td>
      <td className="font-mono text-xs">
        {typeof row.clv_pct === "number" ? (
          <span
            className={row.clv_pct >= 0 ? "text-strong" : "text-nosignal"}
          >
            {row.clv_pct >= 0 ? "+" : ""}
            {row.clv_pct.toFixed(2)}pp
          </span>
        ) : (
          "—"
        )}
      </td>
      <td>
        <ResultBadge result={row.result} />
      </td>
      <td className="font-mono text-xs">
        {typeof row.units === "number" ? (
          <span
            className={
              row.units > 0
                ? "text-strong"
                : row.units < 0
                  ? "text-nosignal"
                  : "text-chalk-300"
            }
          >
            {row.units >= 0 ? "+" : ""}
            {row.units.toFixed(2)}u
          </span>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}


function ResultBadge({
  result,
}: { result: "WIN" | "LOSS" | "PUSH" | null }) {
  if (result === null) {
    return (
      <span className="text-[11px] font-mono uppercase tracking-wider text-chalk-500">
        Pending
      </span>
    );
  }
  const color =
    result === "WIN"
      ? "text-strong"
      : result === "LOSS"
        ? "text-nosignal"
        : "text-chalk-300";
  return (
    <span
      className={`text-[11px] font-mono uppercase tracking-wider ${color}`}
    >
      {result}
    </span>
  );
}


function Pagination({
  current, total, basePath, extra,
}: {
  current: number;
  total: number;
  basePath: string;
  extra: {
    sport: SportKey | "all";
    result: ResultFilter;
    clv: CLVFilter;
  };
}) {
  if (total <= 1) return null;
  const params = new URLSearchParams();
  if (extra.sport !== "all") params.set("sport", extra.sport);
  if (extra.result !== "all") params.set("result", extra.result);
  if (extra.clv !== "all") params.set("clv", extra.clv);
  const baseQs = params.toString();
  const buildHref = (page: number) => {
    const merged = new URLSearchParams(baseQs);
    merged.set("page", String(page));
    return `${basePath}?${merged.toString()}`;
  };

  return (
    <div className="flex items-center justify-between text-xs text-chalk-300">
      <div>
        Page <span className="font-mono text-chalk-100">{current}</span> of{" "}
        <span className="font-mono text-chalk-100">{total}</span>
      </div>
      <div className="flex items-center gap-2">
        {current > 1 ? (
          <Link
            href={buildHref(current - 1)}
            className="px-3 py-1 rounded border border-chalkboard-700/60 text-chalk-100 hover:text-elite hover:border-elite/40"
          >
            ← Prev
          </Link>
        ) : (
          <span className="px-3 py-1 rounded border border-chalkboard-800/60 text-chalk-500">
            ← Prev
          </span>
        )}
        {current < total ? (
          <Link
            href={buildHref(current + 1)}
            className="px-3 py-1 rounded border border-chalkboard-700/60 text-chalk-100 hover:text-elite hover:border-elite/40"
          >
            Next →
          </Link>
        ) : (
          <span className="px-3 py-1 rounded border border-chalkboard-800/60 text-chalk-500">
            Next →
          </span>
        )}
      </div>
    </div>
  );
}

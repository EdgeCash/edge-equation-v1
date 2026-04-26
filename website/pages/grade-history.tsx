import type { GetServerSideProps } from "next";
import Link from "next/link";

import CardShell from "@/components/CardShell";
import GradeBadge from "@/components/GradeBadge";
import Layout from "@/components/Layout";
import StatTile from "@/components/StatTile";
import { api, formatPercent } from "@/lib/api";
import type { GradeStats, HitRateReport } from "@/lib/types";


type Props = {
  all: HitRateReport | null;
  bySport: Record<string, HitRateReport>;
  error: string | null;
};


const TRACKED_SPORTS = ["MLB", "NFL", "NHL", "NBA", "KBO", "NPB"];
const GRADE_ORDER = ["A+", "A", "B", "C", "D", "F"];


export const getServerSideProps: GetServerSideProps<Props> = async () => {
  try {
    const all = await api.hitRate();
    const entries = await Promise.all(
      TRACKED_SPORTS.map(async (s) => [s, await api.hitRate(s)] as const),
    );
    const bySport: Record<string, HitRateReport> = {};
    for (const [s, report] of entries) {
      bySport[s] = report;
    }
    return { props: { all, bySport, error: null } };
  } catch (e: unknown) {
    return {
      props: {
        all: null,
        bySport: {},
        error: e instanceof Error ? e.message : "unknown error",
      },
    };
  }
};


function totalPicks(report: HitRateReport): number {
  let total = 0;
  for (const key in report.by_grade) total += report.by_grade[key].n;
  return total;
}


function overallHitRate(report: HitRateReport): number | null {
  let wins = 0;
  let decided = 0;
  for (const key in report.by_grade) {
    const g = report.by_grade[key];
    wins += g.wins;
    decided += g.n - g.pushes;
  }
  if (decided === 0) return null;
  return wins / decided;
}


function GradeRow({ grade, stats }: { grade: string; stats: GradeStats }) {
  const rateStr = stats.n - stats.pushes > 0
    ? `${(stats.hit_rate * 100).toFixed(1)}%`
    : "—";
  return (
    <div className="grid grid-cols-12 gap-4 items-center border border-edge-line rounded-sm p-4">
      <div className="col-span-3 sm:col-span-2"><GradeBadge grade={grade} /></div>
      <div className="col-span-3 sm:col-span-2 text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
        <div>Picks</div>
        <div className="mt-1 font-mono tabular-nums text-edge-text text-lg normal-case tracking-normal">
          {stats.n}
        </div>
      </div>
      <div className="col-span-3 sm:col-span-2 text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
        <div>Wins</div>
        <div className="mt-1 font-mono tabular-nums text-edge-text text-lg normal-case tracking-normal">
          {stats.wins}
        </div>
      </div>
      <div className="col-span-3 sm:col-span-2 text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
        <div>Pushes</div>
        <div className="mt-1 font-mono tabular-nums text-edge-text text-lg normal-case tracking-normal">
          {stats.pushes}
        </div>
      </div>
      <div className="col-span-12 sm:col-span-4 text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
        <div>Hit Rate</div>
        <div className="mt-1 font-mono tabular-nums text-edge-text text-lg normal-case tracking-normal">
          {rateStr}
        </div>
      </div>
    </div>
  );
}


export default function GradeHistory({ all, bySport, error }: Props) {
  return (
    <Layout
      title="Grade History"
      description="Historical hit rate by grade across every settled Edge Equation pick."
    >
      <div className="annotation mb-4 flex items-center gap-3">
        <span className="text-edge-accent">σ</span>
        <span>Calibration</span>
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Grade{" "}
        <span className="italic text-edge-accent chalk-underline accent-glow">
          History
        </span>
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        Every settled pick, bucketed by the grade the engine assigned it.
        Pushes are excluded from denominators; void bets are excluded entirely.
      </p>

      {error && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
        </div>
      )}

      {all && (
        <div className="mt-10 space-y-10">
          <CardShell eyebrow="All sports" headline="Cumulative">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-6">
              <StatTile label="Total picks" value={String(totalPicks(all))} />
              <StatTile
                label="Overall hit rate"
                value={(() => {
                  const r = overallHitRate(all);
                  return r == null ? "—" : `${(r * 100).toFixed(1)}%`;
                })()}
              />
              <StatTile
                label="Grades recorded"
                value={String(Object.keys(all.by_grade).length)}
              />
            </div>
            <div className="space-y-2">
              {GRADE_ORDER
                .filter((g) => all.by_grade[g])
                .map((g) => (
                  <GradeRow key={g} grade={g} stats={all.by_grade[g]} />
                ))}
              {Object.keys(all.by_grade).length === 0 && (
                <p className="text-edge-textDim">
                  No settled picks yet. Run{" "}
                  <code className="font-mono text-edge-text">
                    python -m edge_equation settle outcomes.csv
                  </code>{" "}
                  after games complete to populate this view.
                </p>
              )}
            </div>
          </CardShell>

          {TRACKED_SPORTS.map((sport) => {
            const report = bySport[sport];
            if (!report || Object.keys(report.by_grade).length === 0) return null;
            return (
              <CardShell key={sport} eyebrow={sport} headline={`${sport} breakdown`}>
                <div className="space-y-2">
                  {GRADE_ORDER
                    .filter((g) => report.by_grade[g])
                    .map((g) => (
                      <GradeRow key={g} grade={g} stats={report.by_grade[g]} />
                    ))}
                </div>
              </CardShell>
            );
          })}
        </div>
      )}

      <p className="mt-12 text-edge-textDim">
        Data comes from the realizations table. Back to{" "}
        <Link
          href="/daily-edge"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          today&apos;s slate
        </Link>{" "}
        or the{" "}
        <Link
          href="/archive"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          archive
        </Link>
        .
      </p>
    </Layout>
  );
}

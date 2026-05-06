/**
 * Reliability — /reliability
 *
 * Server-rendered. Per-sport reliability diagram + Brier breakdown.
 * Each panel reads the sport's CLV-tracked picks log; sports without
 * a populated tracker render an honest "Limited Data" panel.
 *
 * Sits next to /track-record as the calibration counterpart: track
 * record answers "how much did we make?", reliability answers "how
 * well-calibrated were the probabilities?".
 */

import Link from "next/link";

import { ChalkboardBackground } from "../../components/ChalkboardBackground";
import { MetricTip } from "../../components/MetricTip";
import {
  ReliabilityDiagram,
  brierScore,
} from "../../components/ReliabilityDiagram";
import { TransparencyNote } from "../../components/TransparencyNote";
import { SPORTS, SPORT_LABEL, SportKey } from "../../lib/feed";
import { loadAllPicks, bySport } from "../../lib/picks-history";


export const dynamic = "force-dynamic";


export default async function ReliabilityPage() {
  const picks = await loadAllPicks();
  const map = bySport(picks);

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Calibration
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            How well-calibrated are our probabilities?
          </h1>
          <p className="mt-3 text-sm text-chalk-300 max-w-2xl">
            A model that says &quot;70%&quot; should win 70% of the
            time over enough samples. The reliability diagram bins
            graded picks by predicted probability, then plots the
            realized hit rate within each bin against the diagonal
            <em> y = x</em>. Drift away from the diagonal is exactly
            what we want a reader to see.
          </p>
          <p className="mt-3 text-xs text-chalk-500">
            Pair the visual with the scalar:{" "}
            <MetricTip term="brier" />. Lower is better; our publish
            gate is &lt; 0.246.
          </p>
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 space-y-6">
        {SPORTS.map((sport) => {
          const rows = map.get(sport) ?? [];
          const brier = brierScore(rows);
          return (
            <SportPanel
              key={sport}
              sport={sport}
              rows={rows}
              brier={brier}
            />
          );
        })}
      </section>

      <TransparencyNote />
    </>
  );
}


function SportPanel({
  sport, rows, brier,
}: {
  sport: SportKey;
  rows: import("../../lib/picks-history").PickRecord[];
  brier: { score: number | null; n: number };
}) {
  return (
    <div className="chalk-card p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-wider text-chalk-500 font-mono">
            {SPORT_LABEL[sport]}
          </p>
          <h2 className="mt-1 text-xl font-semibold text-chalk-50">
            {SPORT_LABEL[sport]} reliability
          </h2>
        </div>
        <Link
          href={`/sport/${sport}`}
          className="text-xs text-elite hover:underline"
        >
          {SPORT_LABEL[sport]} hub →
        </Link>
      </header>
      <div className="mt-4 grid gap-6 lg:grid-cols-[1fr_240px]">
        <ReliabilityDiagram picks={rows} />
        <div className="space-y-3 text-xs text-chalk-300">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-chalk-500">
              <MetricTip term="brier" label="Brier score" />
            </p>
            <p className="mt-1 font-mono text-lg text-chalk-100">
              {brier.score !== null ? brier.score.toFixed(4) : "—"}
            </p>
            <p className="text-[10px] text-chalk-500">
              {brier.n} graded picks · publish gate &lt; 0.246
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-chalk-500">
              How to read the diagram
            </p>
            <p className="mt-1 text-[11px] text-chalk-300 leading-snug">
              Dots above the dashed diagonal mean the model is
              under-confident in that bucket (we said 60% but won
              70% of the time). Dots below the diagonal mean we&apos;re
              over-confident.
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-chalk-500">
              Sample sizes
            </p>
            <p className="mt-1 text-[11px] text-chalk-300 leading-snug">
              Dot radius scales with the number of graded picks in
              that bucket. Sparse buckets are noisier and shouldn&apos;t
              be over-interpreted.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

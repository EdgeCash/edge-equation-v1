import type { GetStaticProps } from "next";

import LedgerTable from "@/components/LedgerTable";
import Layout from "@/components/Layout";
import TierSummaryStrip from "@/components/TierSummaryStrip";
import type { TrackRecordView } from "@/lib/track-record";
import { loadTrackRecord } from "@/lib/track-record-server";


type Props = { view: TrackRecordView };


/**
 * Public, append-only track record. Every LEAN-and-above pick the
 * engine has ever made, with W/L/Push outcomes and a running record
 * per tier across engines.
 *
 * No paywall. No subscription. The page IS the marketing asset for
 * the eventual paid tier (planned for football season). Sample-size
 * honesty is the whole point — empty tiers say "No settled picks
 * yet" rather than fake 0%.
 */
export const getStaticProps: GetStaticProps<Props> = async () => {
  const view = await loadTrackRecord();
  return {
    props: { view },
    // Re-build whenever the daily exporter pushes new JSON. Keep TTL
    // short so a deploy isn't required for routine updates.
    revalidate: 60 * 5,
  };
};


export default function TrackRecordPage({ view }: Props) {
  const { ledger, summary, isPlaceholder } = view;

  return (
    <Layout
      title="Track Record"
      description="Every LEAN-and-above pick the Edge Equation engines have made — with honest, append-only outcomes."
    >
      <main className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6 sm:py-16">
        {/* ----- Page header ----- */}
        <div className="mb-10 max-w-prose">
          <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-edge-accent">
            Public Ledger
          </div>
          <h1 className="mt-3 font-display text-4xl tracking-tightest leading-[1.05] text-edge-text sm:text-5xl">
            Track Record
          </h1>
          <p className="mt-4 text-edge-textDim">
            Every LEAN-and-above pick the engine produces is logged here, with
            the actual outcome attached. No selective publishing. No
            retroactive edits. The numbers below reflect every pick — wins,
            losses, pushes, and pending games.
          </p>
        </div>

        {/* ----- Honesty disclaimer banner ----- */}
        <div className="mb-10 rounded-sm border border-edge-line bg-ink-900/60 px-5 py-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-edge-accent">
            Honest Reporting
          </div>
          <p className="mt-2 text-sm text-edge-textDim">
            This is data, not betting advice. The picks below are the
            engine's output; outcomes are logged honestly whether they win
            or lose. Past performance does not predict future results.
            For entertainment and informational purposes only.
          </p>
        </div>

        {/* ----- Tier summary cards ----- */}
        <section className="mb-12">
          <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.24em] text-edge-textFaint">
            Running Record · All Engines
          </h2>
          <TierSummaryStrip summary={summary} />
          <p className="mt-3 font-mono text-[10px] text-edge-textFaint">
            Hit rate excludes pushes. Units assumes flat 1u stakes per pick.
          </p>
        </section>

        {/* ----- Empty / placeholder state ----- */}
        {isPlaceholder && (
          <div className="rounded-sm border border-edge-line bg-ink-900/80 p-8 text-center">
            <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-edge-accent">
              No data yet
            </div>
            <p className="mt-3 text-edge-textDim">
              The track-record JSON hasn't been published from the engine
              yet. The daily settlement workflow generates it; check back
              after tomorrow's slate settles.
            </p>
          </div>
        )}

        {/* ----- Ledger table ----- */}
        {!isPlaceholder && (
          <section>
            <h2 className="mb-4 font-mono text-[10px] uppercase tracking-[0.24em] text-edge-textFaint">
              Pick Ledger · {ledger.n_picks} entries
            </h2>
            <LedgerTable picks={ledger.picks} />
            <p className="mt-4 font-mono text-[10px] text-edge-textFaint">
              Last updated{" "}
              {new Date(ledger.generated_at).toLocaleString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
              .
            </p>
          </section>
        )}

        {/* ----- Bottom disclaimer ----- */}
        <section className="mt-16 border-t border-edge-line pt-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-edge-textFaint">
            Disclaimers
          </div>
          <ul className="mt-3 space-y-2 text-sm text-edge-textDim">
            <li>
              <strong className="text-edge-text">Not financial advice.</strong>{" "}
              Edge Equation is a data and content service. Nothing on this
              page constitutes a recommendation to wager money.
            </li>
            <li>
              <strong className="text-edge-text">Honest record.</strong>{" "}
              Picks are appended at the time the engine produces them. They
              are never edited, removed, or backfilled to favor a narrative.
            </li>
            <li>
              <strong className="text-edge-text">Sample size matters.</strong>{" "}
              Hit rates on small samples (under ~50 picks per tier) are
              noisy. Treat all early-season numbers as preliminary.
            </li>
            <li>
              <strong className="text-edge-text">Responsible gaming.</strong>{" "}
              If you or someone you know has a gambling problem, call
              1-800-GAMBLER. Bet responsibly. Only stake what you can
              afford to lose.
            </li>
          </ul>
        </section>
      </main>
    </Layout>
  );
}

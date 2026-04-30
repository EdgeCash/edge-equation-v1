import Link from "next/link";
import type { GetServerSideProps } from "next";

import CardShell from "@/components/CardShell";
import ConvictionKey from "@/components/ConvictionKey";
import Layout from "@/components/Layout";
import PickRow from "@/components/PickRow";
import StatTile from "@/components/StatTile";
import { api, formatDate, formatPercent } from "@/lib/api";
import { tierFromGrade } from "@/lib/conviction";
import type { SlateDetail } from "@/lib/types";


type Props = {
  slate: SlateDetail | null;
  error: string | null;
};


export const getServerSideProps: GetServerSideProps<Props> = async () => {
  try {
    const slate = await api.latestSlate("daily_edge");
    return { props: { slate, error: null } };
  } catch (e: unknown) {
    return {
      props: {
        slate: null,
        error: e instanceof Error ? e.message : "unknown error",
      },
    };
  }
};


function tierCounts(slate: SlateDetail) {
  let elite = 0;
  let strong = 0;
  let other = 0;
  for (const p of slate.picks) {
    const t = tierFromGrade(p.grade);
    if (t === "ELITE") elite++;
    else if (t === "STRONG" || t === "STRONG_NRFI" || t === "STRONG_YRFI") strong++;
    else other++;
  }
  return { elite, strong, other };
}


function topEdge(slate: SlateDetail): string | null {
  let best: number | null = null;
  for (const p of slate.picks) {
    if (!p.edge) continue;
    const n = Number(p.edge);
    if (Number.isNaN(n)) continue;
    if (best == null || n > best) best = n;
  }
  return best == null ? null : String(best);
}


export default function DailyEdge({ slate, error }: Props) {
  const counts = slate ? tierCounts(slate) : null;

  return (
    <Layout
      title="Daily Edge"
      description="Today's Daily Edge slate from the Edge Equation engine. Free, every day."
    >
      <div className="eyebrow mb-4">Free · Every Day</div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
        Daily <span className="italic text-edge-accent">Edge.</span>
      </h1>
      <p className="mt-6 max-w-prose text-edge-textDim text-lg leading-relaxed">
        Today&apos;s board, every pick tagged with a single conviction tier.
        Electric Blue is rare on purpose. Most days, most plays sit below it —
        and that&apos;s the point.
      </p>

      {error && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.22em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
          <p className="mt-3 text-edge-textDim text-sm">
            If you&apos;re running locally, make sure the FastAPI backend is
            up at <code className="font-mono">NEXT_PUBLIC_API_BASE_URL</code>.
          </p>
        </div>
      )}

      {!error && !slate && (
        <div className="mt-12">
          <CardShell
            eyebrow="Awaiting today’s slate"
            headline="No daily_edge slate has been persisted yet."
            subhead="Once the scheduler runs python -m edge_equation daily, today's picks will appear here."
          >
            <p className="text-edge-textDim">
              Follow on{" "}
              <Link
                href="https://x.com/edgeequation"
                className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
              >
                X
              </Link>{" "}
              for posted cards.
            </p>
          </CardShell>
        </div>
      )}

      {slate && counts && (
        <div className="mt-10 space-y-10">
          <CardShell
            eyebrow={`Slate · ${formatDate(slate.generated_at)}`}
            headline={`${slate.picks.length} pick${slate.picks.length === 1 ? "" : "s"} on the board`}
            subhead={`Slate id: ${slate.slate_id}`}
          >
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile label="Top Edge" value={formatPercent(topEdge(slate))} />
              <StatTile label="Electric Blue" value={String(counts.elite)} />
              <StatTile label="Strong" value={String(counts.strong)} />
              <StatTile label="Total picks" value={String(slate.n_picks)} />
            </div>
          </CardShell>

          <section>
            <div className="flex flex-wrap items-end justify-between gap-3 mb-4">
              <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-accent">
                Every pick on today&apos;s board
              </div>
              <Link
                href="/learn"
                className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
              >
                How to read this →
              </Link>
            </div>
            <div className="space-y-3">
              {slate.picks.map((p) => (
                <PickRow key={p.pick_id} pick={p} />
              ))}
            </div>
          </section>

          <section>
            <ConvictionKey />
          </section>
        </div>
      )}

      <p className="mt-12 text-edge-textDim">
        Follow on{" "}
        <Link
          href="https://x.com/edgeequation"
          className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
        >
          X
        </Link>{" "}
        for live cards,{" "}
        <Link
          href="/archive"
          className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
        >
          browse the archive
        </Link>
        , or check{" "}
        <Link
          href="/grade-history"
          className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
        >
          hit rate by tier
        </Link>
        .
      </p>

      <p className="mt-10 font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint max-w-prose leading-relaxed">
        Educational and entertainment use. No guarantees. 21+. Bet within your means.
      </p>
    </Layout>
  );
}

import Link from "next/link";
import type { GetServerSideProps } from "next";

import CardShell from "@/components/CardShell";
import Layout from "@/components/Layout";
import PickRow from "@/components/PickRow";
import StatTile from "@/components/StatTile";
import { api, formatDate, formatPercent } from "@/lib/api";
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


function pickByGrade(slate: SlateDetail): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const p of slate.picks) {
    counts[p.grade] = (counts[p.grade] ?? 0) + 1;
  }
  return counts;
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
  return (
    <Layout
      title="Daily Edge"
      description="Today's Daily Edge slate from the Edge Equation engine."
    >
      <div className="annotation mb-4 flex items-center gap-3">
        <span className="text-edge-accent">∴</span>
        <span>Public · Free · One card per day</span>
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Daily{" "}
        <span className="italic text-edge-accent chalk-underline accent-glow">
          Edge
        </span>
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        A single card, once a day. Model-driven projections across today&apos;s
        slate — fair probability, edge, grade, half-Kelly sizing.
      </p>

      {error && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
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
            eyebrow="Awaiting First Slate"
            headline="No daily_edge slate has been persisted yet."
            subhead="Once the scheduler runs python -m edge_equation daily, today's picks will appear here."
          >
            <p className="text-edge-textDim">
              Follow on{" "}
              <Link
                href="https://x.com/edgeequation"
                className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
              >
                X
              </Link>{" "}
              for posted cards.
            </p>
          </CardShell>
        </div>
      )}

      {slate && (
        <div className="mt-10 space-y-10">
          <CardShell
            eyebrow={`Slate · ${formatDate(slate.generated_at)}`}
            headline={`${slate.picks.length} pick${slate.picks.length === 1 ? "" : "s"}`}
            subhead={`Slate id: ${slate.slate_id}`}
          >
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile label="Top Edge" value={formatPercent(topEdge(slate))} />
              <StatTile label="Picks" value={String(slate.n_picks)} />
              <StatTile
                label="A/A+ Count"
                value={String(
                  (pickByGrade(slate)["A"] ?? 0) + (pickByGrade(slate)["A+"] ?? 0),
                )}
              />
              <StatTile label="Card Type" value={slate.card_type} />
            </div>
          </CardShell>

          <section>
            <div className="annotation mb-4">// every pick in this slate</div>
            <div className="space-y-3">
              {slate.picks.map((p) => (
                <PickRow key={p.pick_id} pick={p} />
              ))}
            </div>
          </section>
        </div>
      )}

      <p className="mt-12 text-edge-textDim">
        Follow on{" "}
        <Link
          href="https://x.com/edgeequation"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          X
        </Link>{" "}
        for live cards,{" "}
        <Link
          href="/archive"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          browse the archive
        </Link>
        , or check{" "}
        <Link
          href="/grade-history"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          hit rate by grade
        </Link>
        .
      </p>
    </Layout>
  );
}

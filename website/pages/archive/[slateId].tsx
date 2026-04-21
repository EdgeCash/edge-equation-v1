import type { GetServerSideProps } from "next";
import Link from "next/link";

import CardShell from "@/components/CardShell";
import Layout from "@/components/Layout";
import PickRow from "@/components/PickRow";
import StatTile from "@/components/StatTile";
import { api, formatDate } from "@/lib/api";
import type { SlateDetail } from "@/lib/types";


type Props = {
  slate: SlateDetail | null;
  error: string | null;
};


export const getServerSideProps: GetServerSideProps<Props> = async (ctx) => {
  const slateId = Array.isArray(ctx.params?.slateId)
    ? ctx.params?.slateId[0]
    : ctx.params?.slateId;
  if (!slateId) return { notFound: true };
  try {
    const slate = await api.getSlate(String(slateId));
    if (slate == null) return { notFound: true };
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


export default function SlateDetailPage({ slate, error }: Props) {
  return (
    <Layout
      title={slate ? `Slate · ${slate.slate_id}` : "Slate"}
      description={slate ? `Every pick in ${slate.slate_id}` : "Slate detail"}
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        <Link
          href="/archive"
          className="hover:text-edge-text transition-colors"
        >
          ← Archive
        </Link>
      </div>

      {error && (
        <div className="mt-6 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
        </div>
      )}

      {slate && (
        <div className="space-y-10">
          <div>
            <h1 className="font-display font-light text-4xl sm:text-5xl tracking-tightest leading-none">
              {slate.slate_id}
            </h1>
            <p className="mt-3 text-edge-textDim">
              {slate.card_type} · generated {formatDate(slate.generated_at)}
              {slate.sport ? ` · ${slate.sport}` : ""}
            </p>
          </div>

          <CardShell eyebrow="Summary" headline={`${slate.picks.length} picks`}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile label="Picks" value={String(slate.picks.length)} />
              <StatTile label="Card Type" value={slate.card_type} />
              <StatTile
                label="Sport"
                value={slate.sport ?? "mixed"}
              />
              <StatTile
                label="Settled"
                value={String(
                  slate.picks.filter(
                    (p) => [0, 50, 100].includes(p.realization),
                  ).length,
                )}
              />
            </div>
          </CardShell>

          <section>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-accent mb-4">
              All picks in this slate
            </div>
            <div className="space-y-3">
              {slate.picks.map((p) => (
                <PickRow key={p.pick_id} pick={p} />
              ))}
              {slate.picks.length === 0 && (
                <p className="text-edge-textDim">
                  No picks persisted in this slate. The engine ran but produced
                  no graded plays.
                </p>
              )}
            </div>
          </section>
        </div>
      )}
    </Layout>
  );
}

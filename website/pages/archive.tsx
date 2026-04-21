import type { GetServerSideProps } from "next";
import Link from "next/link";

import CardShell from "@/components/CardShell";
import Layout from "@/components/Layout";
import { api, formatDate } from "@/lib/api";
import type { SlateSummary } from "@/lib/types";


type Props = {
  slates: SlateSummary[];
  error: string | null;
};


export const getServerSideProps: GetServerSideProps<Props> = async () => {
  try {
    const slates = await api.listSlates({ limit: 100 });
    return { props: { slates, error: null } };
  } catch (e: unknown) {
    return {
      props: {
        slates: [],
        error: e instanceof Error ? e.message : "unknown error",
      },
    };
  }
};


export default function Archive({ slates, error }: Props) {
  return (
    <Layout
      title="Archive"
      description="Every slate the Edge Equation engine has persisted."
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        History
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Archive
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        Every slate the engine has generated — daily and evening, every sport,
        with a link through to the full pick list.
      </p>

      {error && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
        </div>
      )}

      {!error && slates.length === 0 && (
        <div className="mt-10">
          <CardShell eyebrow="Empty" headline="No slates persisted yet">
            <p className="text-edge-textDim">
              Once the scheduler has run at least once, its slates will show up
              here. Trigger a run manually:
            </p>
            <pre className="mt-4 font-mono text-xs text-edge-text bg-ink-950 border border-edge-line rounded-sm p-4 overflow-x-auto">
              python -m edge_equation daily --publish
            </pre>
          </CardShell>
        </div>
      )}

      {slates.length > 0 && (
        <div className="mt-10 space-y-3">
          {slates.map((s) => (
            <Link
              key={s.slate_id}
              href={`/archive/${encodeURIComponent(s.slate_id)}`}
              className="block border border-edge-line rounded-sm p-5 hover:border-edge-accent/60 transition-colors"
            >
              <div className="grid grid-cols-12 gap-4 items-center">
                <div className="col-span-12 sm:col-span-5">
                  <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-accent">
                    {s.card_type}
                  </div>
                  <div className="mt-1 font-display text-lg tracking-tightest text-edge-text">
                    {s.slate_id}
                  </div>
                </div>
                <div className="col-span-6 sm:col-span-3">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                    Generated
                  </div>
                  <div className="mt-1 font-mono text-sm text-edge-text tabular-nums">
                    {formatDate(s.generated_at)}
                  </div>
                </div>
                <div className="col-span-3 sm:col-span-2">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                    Picks
                  </div>
                  <div className="mt-1 font-mono text-lg text-edge-text tabular-nums">
                    {s.n_picks}
                  </div>
                </div>
                <div className="col-span-3 sm:col-span-2 text-right">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                    Sport
                  </div>
                  <div className="mt-1 font-mono text-sm text-edge-text">
                    {s.sport ?? "—"}
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </Layout>
  );
}

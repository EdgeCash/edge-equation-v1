import Link from "next/link";
import Layout from "@/components/Layout";
import CardShell from "@/components/CardShell";

export default function DailyEdge() {
  return (
    <Layout title="Daily Edge" description="Today's public Daily Edge card from the Edge Equation engine.">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Public · Free
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Daily Edge
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        A single card, once a day. Model-driven projections across today&apos;s
        slate — fair probability, edge, grade, half-Kelly sizing.
      </p>

      <div className="mt-12">
        <CardShell
          eyebrow="Today · Public Card"
          headline="Today's Daily Edge"
          subhead="Model-driven projections across today's slate."
        >
          <div className="font-mono text-xs tracking-wide text-edge-textDim space-y-4">
            <div className="border border-dashed border-edge-line rounded-sm p-6">
              <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
                Placeholder
              </div>
              <p className="text-edge-text font-body text-base leading-relaxed">
                API-connected card coming in Phase 6B. Until then, cards ship
                once a day on X, with full picks, grades, and sizing.
              </p>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-4">
              {[
                { label: "Fair Prob", value: "0.618133" },
                { label: "Edge", value: "0.049167" },
                { label: "Grade", value: "A" },
                { label: "½ Kelly", value: "0.0324" },
              ].map((s) => (
                <div key={s.label}>
                  <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                    {s.label}
                  </div>
                  <div className="mt-1 font-mono tabular text-edge-text text-lg">
                    {s.value}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </CardShell>
      </div>

      <p className="mt-8 text-edge-textDim">
        Follow on{" "}
        <Link
          href="https://x.com/edgeequation"
          className="text-edge-accent border-b border-edge-accent/50 hover:border-edge-accent"
        >
          X
        </Link>{" "}
        for live cards.
      </p>
    </Layout>
  );
}

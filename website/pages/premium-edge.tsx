import Link from "next/link";
import Layout from "@/components/Layout";
import CardShell from "@/components/CardShell";

const FEATURES = [
  { title: "Full Distributions", body: "p10 / p50 / p90 and mean from deterministic Monte Carlo simulation." },
  { title: "Letter Grades", body: "A+ / A / B / C ratings on every pick, calibrated to realization buckets." },
  { title: "Kelly Guidance", body: "Half-Kelly, 25% cap, gated at meaningful edge. Sizing built in." },
  { title: "Model Notes", body: "Context on what drove the number and what would change it." },
];

export default function PremiumEdge() {
  return (
    <Layout title="Premium Edge" description="Premium analytics coming soon.">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Launching Soon
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Premium <span className="italic text-edge-accent">Edge</span>
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        Distributions, grades, sizing, and model notes. The same engine that
        powers the public card, with everything unredacted.
      </p>

      <div className="mt-12 grid grid-cols-1 md:grid-cols-2 gap-px bg-edge-line">
        {FEATURES.map((f, i) => (
          <div key={f.title} className="bg-ink-900 p-8">
            <div className="flex items-baseline gap-3 mb-3">
              <span className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                {String(i + 1).padStart(2, "0")}
              </span>
              <h3 className="font-display text-2xl tracking-tightest">{f.title}</h3>
            </div>
            <p className="text-edge-textDim">{f.body}</p>
          </div>
        ))}
      </div>

      <div className="mt-12">
        <CardShell eyebrow="Premium · Coming Soon" headline="Premium analytics launching soon.">
          <div className="space-y-4 text-edge-textDim">
            <p>
              Every Pick carries a deterministic Monte Carlo distribution. The
              card below is a preview of the shape premium subscribers will see.
            </p>
            <div className="hairline pt-4 flex flex-wrap items-center gap-3">
              <Link
                href="https://x.com/edgeequation"
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-text transition-colors"
              >
                Follow on X for launch updates
              </Link>
              <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim">
                No signup yet · No spam
              </span>
            </div>
          </div>
        </CardShell>
      </div>
    </Layout>
  );
}

import Link from "next/link";
import Layout from "@/components/Layout";

export default function Home() {
  return (
    <Layout>
      <section className="pt-10 sm:pt-16 relative">
        {/* Margin formula tag — chalk annotation in the gutter */}
        <div className="annotation mb-6 flex items-center gap-3">
          <span className="text-edge-accent">f(x) =</span>
          <span>Deterministic Sports Analytics · Est. 2026</span>
        </div>

        <h1 className="font-display font-light text-[clamp(3rem,8vw,6.5rem)] leading-[0.95] tracking-tightest">
          Edge{" "}
          <span className="italic text-edge-accent chalk-underline accent-glow">
            Equation
          </span>
        </h1>

        <p className="mt-8 max-w-prose text-edge-textDim text-lg leading-relaxed">
          A formula-driven engine that turns sport-specific inputs into fair
          probabilities, graded edges, and sized positions — no hype, no
          narrative. The same inputs always produce the same output.
        </p>

        <div className="mt-12 flex flex-wrap items-center gap-6">
          <Link
            href="/daily-edge"
            className="group inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-accentMuted transition-colors"
          >
            View Today&apos;s Edge
            <span className="group-hover:translate-x-1 transition-transform">→</span>
          </Link>
          <Link
            href="/about"
            className="font-mono text-xs uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-text transition-colors border-b border-transparent hover:border-edge-accent pb-1"
          >
            How It Works
          </Link>
        </div>
      </section>

      {/* Three-column principle grid */}
      <section className="mt-32">
        <div className="annotation mb-4">// axioms</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-edge-line border border-edge-line">
          {[
            {
              num: "01",
              tag: "∂P/∂x",
              title: "Deterministic",
              body: "Fixed seeds, Decimal math, 28-digit precision. No wobble between runs.",
            },
            {
              num: "02",
              tag: "log(p/1-p)",
              title: "Transparent",
              body: "Every pick carries fair probability, edge, grade, and Kelly. Show your work.",
            },
            {
              num: "03",
              tag: "½ · Kelly",
              title: "Disciplined",
              body: "Half-Kelly, capped at 25%. Clamps on impact and multipliers. Facts. Not Feelings.",
            },
          ].map((p) => (
            <div key={p.num} className="bg-ink-950 p-8 relative group">
              <div className="flex items-baseline justify-between">
                <div className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                  {p.num}
                </div>
                <div className="font-mono text-[10px] text-edge-textDim opacity-70 group-hover:opacity-100 transition-opacity">
                  {p.tag}
                </div>
              </div>
              <h3 className="mt-4 font-display text-2xl tracking-tightest">
                {p.title}
              </h3>
              <p className="mt-3 text-edge-textDim">{p.body}</p>
            </div>
          ))}
        </div>
      </section>
    </Layout>
  );
}

import Layout from "@/components/Layout";

export default function About() {
  return (
    <Layout title="About">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        Manifesto
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        About Edge <span className="italic text-edge-accent">Equation</span>
      </h1>

      <div className="mt-12 grid grid-cols-1 md:grid-cols-[1fr_auto_2fr] gap-10 items-start">
        <aside className="md:sticky md:top-12">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-textDim">
            Core Principle
          </div>
          <blockquote className="mt-3 font-display text-3xl tracking-tightest italic text-edge-accent leading-tight">
            &ldquo;Facts.
            <br />
            Not Feelings.&rdquo;
          </blockquote>
        </aside>

        <div className="hidden md:block w-px bg-edge-line self-stretch" />

        <div className="space-y-6 text-edge-textDim max-w-prose leading-relaxed">
          <p>
            Edge Equation is a deterministic sports analytics engine. Given the
            same inputs, it always produces the same output — no hidden
            randomness, no warm-takes, no narrative shortcuts. Every pick is
            the result of explicit math: Bradley-Terry for matchups, Dixon-Coles
            adjustments for totals, Poisson for BTTS, Kelly for sizing, all
            running on 28-digit Decimal precision.
          </p>
          <p>
            There is no hype here. We don&apos;t sell &ldquo;locks,&rdquo;
            &ldquo;heaters,&rdquo; or &ldquo;gamblers&rsquo;
            intuition.&rdquo; We publish fair probabilities, edge relative to
            market, grades, and sized positions. When the model is wrong, the
            record shows it. When it&apos;s right, the record shows that too.
          </p>
          <p>
            The edge isn&apos;t magic. It&apos;s discipline — a formula you can
            audit, executed the same way every day.
          </p>
        </div>
      </div>
    </Layout>
  );
}

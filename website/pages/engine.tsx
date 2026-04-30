import Link from "next/link";
import Layout from "@/components/Layout";
import ConvictionKey from "@/components/ConvictionKey";

const STAGES = [
  {
    num: "01",
    title: "Inputs",
    body: "Box scores, ratings, weather, lineups, market lines, line history. We pull the same fields the same way every day so yesterday’s output and today’s output stay comparable.",
  },
  {
    num: "02",
    title: "Models",
    body: "Sport-specific math: Bradley-Terry for matchups, Dixon-Coles for totals, Poisson for first-inning runs, plain old logistic regression where it’s the right tool. Nothing flashy. All audited.",
  },
  {
    num: "03",
    title: "Probabilities",
    body: "Each model produces a fair probability for the question it was built to answer. No model is asked to answer a question it wasn’t built for.",
  },
  {
    num: "04",
    title: "Edge",
    body: "Fair probability is compared to current market lines. The gap is the edge. If the gap closes before tip-off, we re-score it. If it disappears, the pick comes off the board.",
  },
  {
    num: "05",
    title: "Conviction",
    body: "Every pick is mapped to a conviction tier. The Electric Blue, Deep Green, Red, Amber, and Slate buckets are not opinions — they’re thresholds applied the same way every time.",
  },
  {
    num: "06",
    title: "Sizing",
    body: "Half-Kelly, capped at 25% of full Kelly. Edges below a meaningful threshold don’t get sized at all. The engine would rather pass than guess.",
  },
];

const FAQ = [
  {
    q: "Is this AI?",
    a: "No. There’s no LLM in the stack. The engine is closed-form math and a handful of well-understood statistical models. We can show you the equations.",
  },
  {
    q: "Why do you publish picks if you’re not selling them?",
    a: "Because publishing forces honesty. A model that runs in private can quietly forget the bad weeks. A model that posts every day can’t.",
  },
  {
    q: "What sports do you cover?",
    a: "We cover what the data supports. Today that’s primarily MLB first-inning markets and select totals, with college football and NBA in development. We won’t expand a market until we trust the inputs.",
  },
  {
    q: "Do you account for line movement?",
    a: "Yes. Picks are timestamped against the line that existed when we graded them. If you bet a worse number, the realized edge is worse. We’re explicit about that.",
  },
  {
    q: "How often is the model wrong?",
    a: "Often enough that one losing day means nothing. Look at conviction-tier hit rates over a meaningful sample, not single picks. The grade history page shows the long-run record.",
  },
  {
    q: "Can I see the code?",
    a: "Parts of it, yes — and we publish enough about the methodology that a serious bettor can reproduce the logic. The engine itself is closed source.",
  },
];

export default function Engine() {
  return (
    <Layout
      title="The Engine"
      description="How the Edge Equation engine turns inputs into conviction-tier picks."
    >
      {/* Hero */}
      <section>
        <div className="eyebrow mb-4">The Engine</div>
        <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
          A model that{" "}
          <span className="italic text-edge-accent">shows its work.</span>
        </h1>
        <p className="mt-8 max-w-prose text-edge-textDim text-lg leading-relaxed">
          The engine is deterministic. The same inputs always produce the same
          output. There is no special sauce, no hidden randomness, no warm
          take. There&apos;s a pipeline — and we&apos;ll walk you through it.
        </p>
      </section>

      {/* Pipeline */}
      <section className="mt-16">
        <div className="eyebrow mb-3">Pipeline</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          Six stages. No shortcuts.
        </h2>
        <ol className="mt-10 grid gap-px bg-edge-line md:grid-cols-2 lg:grid-cols-3">
          {STAGES.map((s) => (
            <li key={s.num} className="bg-ink-950 p-7">
              <div className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                {s.num}
              </div>
              <h3 className="mt-3 font-display text-2xl tracking-tightest">
                {s.title}
              </h3>
              <p className="mt-3 text-edge-textDim leading-relaxed">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* Conviction key */}
      <section className="mt-24">
        <div className="grid gap-10 md:grid-cols-[1fr_1.4fr] items-start">
          <div>
            <div className="eyebrow mb-3">Output</div>
            <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
              Every output gets one tier.
            </h2>
            <p className="mt-4 text-edge-textDim leading-relaxed max-w-prose">
              A pick is not a vibe. It is a tier. Tiers are assigned by fixed
              thresholds on edge, model agreement, and input quality. They do
              not depend on how the model is &quot;feeling&quot; about a slate.
            </p>
          </div>
          <ConvictionKey />
        </div>
      </section>

      {/* Methodology */}
      <section className="mt-24">
        <div className="eyebrow mb-3">Methodology</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          What we use, in plain English.
        </h2>
        <div className="mt-10 grid gap-6 md:grid-cols-2 max-w-prose-wide">
          <div className="border border-edge-line rounded-sm p-6 bg-ink-900/60">
            <h3 className="font-display text-xl tracking-tightest text-edge-text">
              Bradley-Terry
            </h3>
            <p className="mt-2 text-edge-textDim leading-relaxed">
              A standard pairwise-comparison model. Used to estimate matchup
              win probabilities from team strength ratings and home advantage.
            </p>
          </div>
          <div className="border border-edge-line rounded-sm p-6 bg-ink-900/60">
            <h3 className="font-display text-xl tracking-tightest text-edge-text">
              Dixon-Coles
            </h3>
            <p className="mt-2 text-edge-textDim leading-relaxed">
              An adjustment to Poisson scoring to better capture low-score
              correlation. Used for total-points markets where the tails matter.
            </p>
          </div>
          <div className="border border-edge-line rounded-sm p-6 bg-ink-900/60">
            <h3 className="font-display text-xl tracking-tightest text-edge-text">
              Half-Kelly
            </h3>
            <p className="mt-2 text-edge-textDim leading-relaxed">
              Bet sizing scaled to half of the full Kelly fraction. Caps
              variance, slows drawdowns, and keeps the bankroll in the game.
            </p>
          </div>
          <div className="border border-edge-line rounded-sm p-6 bg-ink-900/60">
            <h3 className="font-display text-xl tracking-tightest text-edge-text">
              Decimal precision
            </h3>
            <p className="mt-2 text-edge-textDim leading-relaxed">
              The engine runs on 28-digit Decimal arithmetic, not floats.
              Floating-point drift is tiny per pick and enormous over a season.
              We refuse to ship a number we can&apos;t reproduce exactly.
            </p>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="mt-24">
        <div className="eyebrow mb-3">Frequently asked</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          The questions we get most.
        </h2>
        <div className="mt-10 divide-y divide-edge-line border-y border-edge-line">
          {FAQ.map((item) => (
            <details key={item.q} className="group py-5">
              <summary className="cursor-pointer list-none flex items-center justify-between gap-4">
                <span className="font-display text-xl tracking-tightest text-edge-text">
                  {item.q}
                </span>
                <span className="font-mono text-edge-accent text-lg group-open:rotate-45 transition-transform">
                  +
                </span>
              </summary>
              <p className="mt-3 text-edge-textDim leading-relaxed max-w-prose">
                {item.a}
              </p>
            </details>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mt-24 border-t border-edge-line pt-16 flex flex-wrap gap-4 items-center">
        <Link
          href="/daily-edge"
          className="inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-text transition-colors"
        >
          See the engine&apos;s output today →
        </Link>
        <Link
          href="/grade-history"
          className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
        >
          Look at the historical record
        </Link>
      </section>
    </Layout>
  );
}

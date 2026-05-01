import Link from "next/link";
import Layout from "@/components/Layout";
import ConvictionBadge from "@/components/ConvictionBadge";
import ConvictionKey from "@/components/ConvictionKey";

const HOW_WE_CALCULATE = [
  {
    title: "Edge size",
    body:
      "How far our fair probability sits from the market's implied probability, after vig. Bigger gaps push a pick up the tier ladder.",
  },
  {
    title: "Model agreement",
    body:
      "Most markets are scored by more than one model. When the models agree, conviction goes up. When they disagree, the pick falls or we pass.",
  },
  {
    title: "Input quality",
    body:
      "Late lineup changes, weather updates, line movement, and missing fields all penalise a pick's tier — even when the headline edge looks juicy.",
  },
  {
    title: "Market stability",
    body:
      "If the line is moving fast against our number, the edge may already be gone. Conviction is graded against the line we can still get, not yesterday's number.",
  },
];

export default function Conviction() {
  return (
    <Layout
      title="The Conviction System"
      description="How we tier every pick on a single scale, with Electric Blue reserved for our highest-conviction calls on either side."
    >
      <section>
        <div className="eyebrow mb-4">The Conviction System</div>
        <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
          One scale. <span className="italic text-edge-accent">Both sides.</span>
        </h1>
        <p className="mt-8 max-w-prose text-edge-textDim text-lg leading-relaxed">
          Every pick we publish — across First Inning, props, and full-game
          markets — is tagged with a single conviction tier. Electric Blue is
          the top of the ladder, and it sits at the top on{" "}
          <em className="text-edge-text not-italic">either side</em> of a
          market: a strong NRFI lean and a strong YRFI lean can both reach
          Electric Blue when the math gets there.
        </p>
      </section>

      {/* Electric Blue callout */}
      <section className="mt-12">
        <div className="border border-conviction-elite/40 bg-conviction-eliteSoft/40 rounded-sm p-6 sm:p-8 shadow-elite-glow">
          <div className="flex flex-wrap items-center gap-4">
            <ConvictionBadge tier="ELITE" size="md" />
            <div className="font-display text-2xl sm:text-3xl tracking-tightest text-edge-text">
              Electric Blue is the top tier — period.
            </div>
          </div>
          <p className="mt-4 text-edge-textDim leading-relaxed max-w-prose">
            We don&apos;t reserve Electric Blue for one side of a market. If
            the data says NRFI is the right side and the edge clears every
            gate, that pick is Electric Blue. If YRFI clears the same gates
            tomorrow, that pick is Electric Blue too. The colour is a verdict
            on conviction, not direction.
          </p>
        </div>
      </section>

      {/* Visual key */}
      <section className="mt-14">
        <div className="eyebrow mb-3">The Key</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          Six tiers, applied the same way every day.
        </h2>
        <div className="mt-8">
          <ConvictionKey />
        </div>
      </section>

      {/* How we calculate */}
      <section className="mt-20">
        <div className="eyebrow mb-3">How We Calculate</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          What pushes a pick up the ladder.
        </h2>
        <p className="mt-5 max-w-prose text-edge-textDim leading-relaxed">
          Conviction is not a vote. It is a fixed function of four inputs.
          Same thresholds, same logic, every slate.
        </p>
        <ol className="mt-10 grid gap-px bg-edge-line md:grid-cols-2">
          {HOW_WE_CALCULATE.map((item, i) => (
            <li key={item.title} className="bg-ink-950 p-7">
              <div className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                {String(i + 1).padStart(2, "0")}
              </div>
              <h3 className="mt-3 font-display text-2xl tracking-tightest">
                {item.title}
              </h3>
              <p className="mt-3 text-edge-textDim leading-relaxed">
                {item.body}
              </p>
            </li>
          ))}
        </ol>
      </section>

      {/* How to use it */}
      <section className="mt-20">
        <div className="eyebrow mb-3">How To Use It</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          Read the colour, then read your bankroll.
        </h2>
        <div className="mt-8 grid gap-6 md:grid-cols-2 max-w-prose-wide">
          <div className="border border-conviction-elite/40 bg-conviction-eliteSoft/30 rounded-sm p-6">
            <div className="flex items-center gap-3">
              <ConvictionBadge tier="ELITE" />
              <span className="font-display text-xl tracking-tightest text-edge-text">
                Electric Blue
              </span>
            </div>
            <p className="mt-3 text-edge-textDim leading-relaxed text-sm">
              Our highest-conviction calls. Rare on purpose. Many days the
              board has none.
            </p>
          </div>
          <div className="border border-conviction-strong/40 bg-conviction-strongSoft/30 rounded-sm p-6">
            <div className="flex items-center gap-3">
              <ConvictionBadge tier="STRONG" />
            </div>
            <p className="mt-3 text-edge-textDim leading-relaxed text-sm">
              Strong directional reads — solid edge with grade-A inputs. The
              kind of play that runs every day on the board, regardless of
              which side of the market the model lands on.
            </p>
          </div>
          <div className="border border-conviction-moderate/40 bg-conviction-moderateSoft/30 rounded-sm p-6">
            <div className="flex items-center gap-3">
              <ConvictionBadge tier="MODERATE" />
              <span className="font-display text-xl tracking-tightest text-edge-text">
                Amber · Moderate
              </span>
            </div>
            <p className="mt-3 text-edge-textDim leading-relaxed text-sm">
              Modest edge or a noisier signal. Published for transparency, not
              as a recommendation.
            </p>
          </div>
          <div className="border border-edge-line bg-ink-900/60 rounded-sm p-6">
            <div className="flex items-center gap-3">
              <ConvictionBadge tier="LEAN" />
              <ConvictionBadge tier="NO_PLAY" />
            </div>
            <p className="mt-3 text-edge-textDim leading-relaxed text-sm">
              Slate is a small directional lean — logged for the record. Red
              means No Play: the modeled edge is too small to matter, or the
              model and market agree. We pass and say so.
            </p>
          </div>
        </div>
      </section>

      {/* Honesty note */}
      <section className="mt-20 max-w-prose">
        <div className="border border-edge-line rounded-sm bg-ink-900/60 p-7">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-3">
            What we won&apos;t do
          </div>
          <ul className="space-y-2 text-edge-textDim leading-relaxed">
            <li>— We won&apos;t inflate a pick&apos;s tier to make a slate look better.</li>
            <li>— We won&apos;t hide losing tiers in the public record.</li>
            <li>— We won&apos;t change thresholds during a slate.</li>
            <li>— We won&apos;t bet a tier we wouldn&apos;t recommend you bet.</li>
          </ul>
        </div>
      </section>

      {/* CTA */}
      <section className="mt-20 border-t border-edge-line pt-12 flex flex-wrap items-center gap-4">
        <Link
          href="/daily-edge"
          className="inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-text transition-colors"
        >
          See today&apos;s board →
        </Link>
        <Link
          href="/learn/reading-the-conviction-tiers"
          className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
        >
          How to use the tiers in your own bankroll math
        </Link>
      </section>

      <p className="mt-12 font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint max-w-prose leading-relaxed">
        Public testing, v1. Conviction tiers reflect modeled edge — they are
        not guarantees. Bet within your means. 21+.
      </p>
    </Layout>
  );
}

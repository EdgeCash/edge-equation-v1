import Link from "next/link";
import Layout from "@/components/Layout";
import ConvictionKey from "@/components/ConvictionKey";

const LESSONS = [
  {
    track: "Foundations",
    items: [
      {
        title: "What an edge actually is",
        time: "5 min read",
        summary:
          "The gap between a fair probability and an implied probability. Why a 53% pick at -110 is a real edge and a 60% pick at -200 isn’t.",
      },
      {
        title: "Reading American odds without a calculator",
        time: "4 min read",
        summary:
          "A simple mental model for converting +110, -135, -180 to implied probabilities in your head. It will make every other lesson easier.",
      },
      {
        title: "Variance, not vibes",
        time: "6 min read",
        summary:
          "Why a profitable bettor can lose for a month and a losing bettor can win for a week. What sample sizes actually mean.",
      },
    ],
  },
  {
    track: "Bankroll",
    items: [
      {
        title: "Unit sizing for humans",
        time: "5 min read",
        summary:
          "How to size a unit based on your real bankroll, your real income, and the kind of variance you can actually live with.",
      },
      {
        title: "Kelly, half-Kelly, and why we cap it",
        time: "8 min read",
        summary:
          "Full Kelly is mathematically optimal and emotionally devastating. Here is why we run at half-Kelly with a 25% cap and you probably should too.",
      },
      {
        title: "Drawdowns are part of the deal",
        time: "5 min read",
        summary:
          "Even a great model can go -10 units in a stretch. How to recognize a normal drawdown vs. a broken process.",
      },
    ],
  },
  {
    track: "Process",
    items: [
      {
        title: "The case for ‘no play’",
        time: "4 min read",
        summary:
          "Saying nothing is a position. Skipped bets are not missed opportunities — they are the discipline that makes the bets you do place mean something.",
      },
      {
        title: "Line shopping is the cheapest edge",
        time: "5 min read",
        summary:
          "The same play at -105 vs. -120 is a different bet. Holding multiple books is the single highest-ROI move most bettors never make.",
      },
      {
        title: "How to read a slate without getting sucked in",
        time: "6 min read",
        summary:
          "A practical workflow for going from ‘ten games tonight’ to ‘two bets, sized correctly, recorded properly’ without falling for the parlay screen.",
      },
    ],
  },
  {
    track: "The Edge Equation Way",
    items: [
      {
        title: "Reading the conviction tiers",
        time: "3 min read",
        summary:
          "What Electric Blue actually means. Why a Lean isn’t a recommendation. How to use the tiers in your own bankroll math.",
      },
      {
        title: "How to use the daily board",
        time: "4 min read",
        summary:
          "We post a slate. You don’t have to bet all of it — and most days, you shouldn’t. Here is how to filter the board to fit your bankroll and risk tolerance.",
      },
      {
        title: "Reading the grade history page",
        time: "5 min read",
        summary:
          "Hit rate by tier, expected vs. realized, and what to look at before deciding whether to trust the model going forward.",
      },
    ],
  },
];

export default function Learn() {
  return (
    <Layout
      title="Learn"
      description="Bankroll, variance, Kelly, line shopping, and the unsexy fundamentals that actually matter."
    >
      <section>
        <div className="eyebrow mb-4">Learn</div>
        <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
          The fundamentals nobody on the timeline{" "}
          <span className="italic text-edge-accent">wants to post.</span>
        </h1>
        <p className="mt-8 max-w-prose text-edge-textDim text-lg leading-relaxed">
          Picks are easy to find. Process is harder. The lessons below are the
          stuff we wish we&apos;d learned in year one — bankroll, variance,
          sizing, line shopping, when to pass. Free. Always.
        </p>
      </section>

      {/* Quick orientation */}
      <section className="mt-16">
        <div className="grid gap-10 md:grid-cols-[1fr_1.3fr] items-start">
          <div>
            <div className="eyebrow mb-3">Start here</div>
            <h2 className="font-display text-3xl tracking-tightest leading-tight">
              How to read the board.
            </h2>
            <p className="mt-4 text-edge-textDim leading-relaxed max-w-prose">
              Every pick we publish carries one conviction tier. Before you
              bet a single number, get fluent with what the colors mean.
            </p>
            <Link
              href="/daily-edge"
              className="mt-5 inline-block font-mono text-xs uppercase tracking-[0.22em] text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent pb-1"
            >
              See today&apos;s board →
            </Link>
          </div>
          <ConvictionKey />
        </div>
      </section>

      {/* Lessons */}
      <section className="mt-24">
        <div className="eyebrow mb-3">Lessons</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          Four tracks. Twelve short reads.
        </h2>

        <div className="mt-12 space-y-16">
          {LESSONS.map((track, idx) => (
            <div key={track.track}>
              <div className="flex items-baseline gap-4 mb-6">
                <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-edge-accent">
                  Track {String(idx + 1).padStart(2, "0")}
                </span>
                <h3 className="font-display text-2xl sm:text-3xl tracking-tightest">
                  {track.track}
                </h3>
              </div>
              <div className="grid gap-px bg-edge-line md:grid-cols-3">
                {track.items.map((item) => (
                  <article
                    key={item.title}
                    className="bg-ink-950 p-6 hover:bg-ink-900 transition-colors flex flex-col"
                  >
                    <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
                      {item.time}
                    </div>
                    <h4 className="mt-3 font-display text-xl tracking-tightest text-edge-text leading-snug">
                      {item.title}
                    </h4>
                    <p className="mt-3 text-edge-textDim leading-relaxed text-sm flex-1">
                      {item.summary}
                    </p>
                    <div className="mt-5 font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
                      Coming soon
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Disclaimer */}
      <section className="mt-24 border-t border-edge-line pt-10">
        <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint max-w-prose leading-relaxed">
          Educational content. Nothing on this page is a recommendation to
          place a specific wager. Bet within your means. 21+.
        </p>
      </section>
    </Layout>
  );
}

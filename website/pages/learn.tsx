import Link from "next/link";
import Layout from "@/components/Layout";
import ConvictionKey from "@/components/ConvictionKey";
import {
  articlesByTrack,
  type LearnTrack,
} from "@/lib/learn-content";

const TRACKS: LearnTrack[] = [
  "Foundations",
  "Bankroll",
  "Process",
  "The Edge Equation Way",
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
              href="/learn/reading-the-conviction-tiers"
              className="mt-5 inline-block font-mono text-xs uppercase tracking-[0.22em] text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent pb-1"
            >
              Read: Reading the conviction tiers →
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
          {TRACKS.map((track, idx) => {
            const items = articlesByTrack(track);
            return (
              <div key={track}>
                <div className="flex items-baseline gap-4 mb-6">
                  <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-edge-accent">
                    Track {String(idx + 1).padStart(2, "0")}
                  </span>
                  <h3 className="font-display text-2xl sm:text-3xl tracking-tightest">
                    {track}
                  </h3>
                </div>
                <div className="grid gap-px bg-edge-line md:grid-cols-3">
                  {items.map((item) => (
                    <Link
                      key={item.slug}
                      href={`/learn/${item.slug}`}
                      className="bg-ink-950 p-6 hover:bg-ink-900 transition-colors flex flex-col group"
                    >
                      <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
                        {item.time}
                      </div>
                      <h4 className="mt-3 font-display text-xl tracking-tightest text-edge-text leading-snug group-hover:text-edge-accent transition-colors">
                        {item.title}
                      </h4>
                      <p className="mt-3 text-edge-textDim leading-relaxed text-sm flex-1">
                        {item.summary}
                      </p>
                      <div className="mt-5 font-mono text-[10px] uppercase tracking-[0.22em] text-edge-accent border-b border-transparent group-hover:border-edge-accent pb-0.5 self-start">
                        Read →
                      </div>
                    </Link>
                  ))}
                </div>
              </div>
            );
          })}
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

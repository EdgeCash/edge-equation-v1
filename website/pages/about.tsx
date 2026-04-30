import Link from "next/link";
import Layout from "@/components/Layout";

export default function About() {
  return (
    <Layout
      title="Our Story"
      description="How Edge Equation evolved from a personal betting tool into a transparent platform for helping people become better bettors."
    >
      {/* Hero */}
      <section>
        <div className="eyebrow mb-4">Our Story</div>
        <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
          Edge <span className="italic text-edge-accent">Equation.</span>
        </h1>
        <p className="mt-6 max-w-prose text-edge-textDim text-lg leading-relaxed">
          I started Edge Equation in early 2026 with one simple goal: build a
          better way for me to bet.
        </p>
      </section>

      {/* The story, verbatim. */}
      <section className="mt-16 max-w-prose">
        <div className="space-y-6 text-edge-textDim text-[17px] leading-[1.7]">
          <p>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mr-2">
              V1
            </span>
            was pure self-interest. I wanted an engine that could cut through
            the noise and give me an actual edge. No hype, no gut feelings —
            just data.
          </p>
          <p>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mr-2">
              V2
            </span>
            was the turning point. As I built deeper models and started
            digging into the &ldquo;why&rdquo; behind every projection, I
            realized something important: the real value wasn&apos;t just the
            picks. It was understanding the process. The features, the
            calibration, the honest strengths and limitations of the data.
          </p>
          <p>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mr-2">
              V3
            </span>
            turned it into a brand. I wanted to share the work cleanly and
            professionally.
          </p>
          <p>
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-elite mr-2">
              V4
            </span>
            — where we are now — is the version I&apos;m most proud of.
          </p>
        </div>

        <div className="mt-10 border-l-2 border-conviction-elite pl-6 space-y-3">
          <p className="font-display text-2xl sm:text-3xl tracking-tightest leading-snug text-edge-text">
            This is no longer about selling picks.
          </p>
          <p className="font-display text-2xl sm:text-3xl tracking-tightest leading-snug italic text-edge-accent">
            It&apos;s about helping serious bettors get better.
          </p>
        </div>

        <div className="mt-10 space-y-6 text-edge-textDim text-[17px] leading-[1.7]">
          <p>
            We still publish picks every day. You can tail them if you want.
          </p>
          <p>
            But our real mission is to show you the work behind them — the
            data, the reasoning, the calibration, the honest confidence levels
            — so you can learn to think for yourself and make stronger
            decisions.
          </p>
          <p className="text-edge-text">
            One well-understood, high-conviction bet is worth more than twenty
            blind tails.
          </p>
          <p>
            We believe in{" "}
            <span className="text-edge-accent italic">Facts. Not Feelings.</span>
          </p>
        </div>

        <div className="mt-12 border border-edge-line rounded-sm bg-ink-900/60 p-7">
          <p className="font-display text-2xl tracking-tightest text-edge-text">
            Welcome to Edge Equation.
          </p>
          <div className="mt-4 space-y-3 text-edge-textDim leading-relaxed">
            <p>We&apos;re still early. We&apos;re still learning every day.</p>
            <p>
              But we&apos;re committed to doing this the right way — with
              transparency, patience, and respect for your bankroll.
            </p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="mt-20 border-t border-edge-line pt-14 max-w-prose">
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          See the work for yourself.
        </h2>
        <div className="mt-6 flex flex-wrap items-center gap-5">
          <Link
            href="/daily-edge"
            className="inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-text transition-colors"
          >
            Today&apos;s Daily Edge →
          </Link>
          <Link
            href="/conviction"
            className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
          >
            How conviction works
          </Link>
          <Link
            href="/learn"
            className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
          >
            Start learning
          </Link>
        </div>
      </section>
    </Layout>
  );
}

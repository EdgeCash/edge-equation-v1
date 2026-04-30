import Link from "next/link";
import Layout from "@/components/Layout";

const CHAPTERS = [
  {
    id: "v1",
    badge: "V1",
    year: "2022 — 2023",
    title: "A model for one.",
    subtitle: "Personal betting tool",
    body: [
      "Edge Equation started as a single-person project — a Python notebook and a stubborn refusal to bet on vibes. The first version was just a way to keep ourselves honest. Take an input, do the math, get a number. If the number disagreed with what we wanted to do, the number won.",
      "There was no website, no audience, no brand. Just a tool. The point was discipline: write down the process before placing the bet, and then let the process decide.",
    ],
  },
  {
    id: "v2",
    badge: "V2",
    year: "2024",
    title: "The picks weren’t the point.",
    subtitle: "The process is the value",
    body: [
      "After a year of running the model, the realization hit slowly and then all at once: the picks were the least interesting output. The model was right, and the model was wrong, and over enough trials those evened out into something close to break-even-plus-a-bit. Normal stuff.",
      "What actually changed our outcomes wasn’t any single number. It was the process. Sizing every play the same way. Saying ‘no play’ when there wasn’t an edge. Logging every result, including the ugly ones. We started telling friends to copy the workflow rather than copy the picks. The picks decay. The process compounds.",
    ],
  },
  {
    id: "v3",
    badge: "V3",
    year: "2025",
    title: "A name for the discipline.",
    subtitle: "The brand",
    body: [
      "V3 was when Edge Equation became a brand instead of a folder of scripts. We gave the engine a name. We wrote down the principles. We picked a tagline — Facts. Not Feelings. — because everything we believed about the work fit in those four words.",
      "We launched a small public site. We posted slates. We started getting questions, and the questions were almost never ‘what’s the lock?’ They were ‘why this number?’ and ‘how should I size this?’ The audience was telling us what they actually wanted. We listened.",
    ],
  },
  {
    id: "v4",
    badge: "V4",
    year: "2026 — now",
    title: "Build better bettors.",
    subtitle: "What we do today",
    body: [
      "V4 is the version that finally matches the mission. Edge Equation is not a pick-selling service. It is a transparent data resource and education platform for people who want to think more carefully about the bets they make.",
      "We publish the daily Electric Blue board for free. We explain how the engine works. We teach the unsexy fundamentals — bankroll, variance, Kelly, line shopping, when to pass — that nobody on the timeline wants to talk about. The premium tier exists for people who want a deeper look at the data and the reasoning. It does not contain a secret pick we hold back from the public board. There is no secret pick.",
      "If we do this right, the people who follow Edge Equation become better bettors than they would have been without us — even on the days they ignore our calls entirely. That is the bar.",
    ],
  },
];

const PRINCIPLES = [
  {
    title: "Transparency over theatre.",
    body:
      "We show the inputs, the math, and the record. When we’re wrong, the record is wrong with us.",
  },
  {
    title: "Education over hype.",
    body:
      "‘Locks’ and ‘heaters’ sell subscriptions and ruin bankrolls. We will not use that language.",
  },
  {
    title: "Process over picks.",
    body:
      "Anyone can be right on a Tuesday. Staying disciplined for a season is what actually matters.",
  },
  {
    title: "Humility about variance.",
    body:
      "An honest model will have losing weeks. We won’t hide them, and we won’t blame the slate.",
  },
];

export default function About() {
  return (
    <Layout
      title="Our Story"
      description="How Edge Equation evolved from a personal model into a platform for building better bettors."
    >
      {/* Hero */}
      <section>
        <div className="eyebrow mb-4">Our Story</div>
        <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
          We didn&apos;t set out to sell picks.
          <br />
          <span className="italic text-edge-accent">We set out to think more clearly.</span>
        </h1>
        <p className="mt-8 max-w-prose text-edge-textDim text-lg leading-relaxed">
          Edge Equation has gone through four versions in four years. Each one
          was the answer to a question the previous one couldn&apos;t answer.
          This is the whole story — the wins, the wrong turns, and what we
          actually believe today.
        </p>
      </section>

      {/* Pull quote */}
      <section className="mt-16">
        <blockquote className="border-l-2 border-conviction-elite pl-6 max-w-prose">
          <p className="font-display text-2xl sm:text-3xl tracking-tightest leading-snug text-edge-text italic">
            &ldquo;The model is the cheap part. The discipline to follow it on
            the days it tells you to do nothing — that&apos;s the entire game.&rdquo;
          </p>
        </blockquote>
      </section>

      {/* Timeline of versions */}
      <section className="mt-20">
        <div className="space-y-14">
          {CHAPTERS.map((c, idx) => (
            <article
              key={c.id}
              className="grid grid-cols-1 md:grid-cols-[180px_1fr] gap-8 md:gap-12 border-t border-edge-line pt-10"
            >
              <div>
                <div
                  className={
                    "font-mono text-[10px] uppercase tracking-[0.28em] " +
                    (idx === CHAPTERS.length - 1
                      ? "text-conviction-elite"
                      : "text-edge-accent")
                  }
                >
                  {c.badge} · {c.year}
                </div>
                <div className="mt-3 font-mono text-[11px] uppercase tracking-[0.22em] text-edge-textFaint">
                  {c.subtitle}
                </div>
              </div>
              <div>
                <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
                  {c.title}
                </h2>
                <div className="mt-5 space-y-4 text-edge-textDim leading-relaxed max-w-prose">
                  {c.body.map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      {/* Principles */}
      <section className="mt-24">
        <div className="eyebrow mb-3">What we believe</div>
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
          Four rules we won&apos;t bend.
        </h2>
        <div className="mt-10 grid grid-cols-1 md:grid-cols-2 gap-px bg-edge-line">
          {PRINCIPLES.map((p, i) => (
            <div key={p.title} className="bg-ink-950 p-8">
              <div className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                {String(i + 1).padStart(2, "0")}
              </div>
              <h3 className="mt-4 font-display text-2xl tracking-tightest">
                {p.title}
              </h3>
              <p className="mt-3 text-edge-textDim leading-relaxed">{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* What we’re NOT */}
      <section className="mt-24 grid gap-10 md:grid-cols-2">
        <div className="border border-conviction-fade/30 bg-conviction-fadeSoft/40 rounded-sm p-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-fade">
            We are not
          </div>
          <ul className="mt-4 space-y-3 text-edge-textDim leading-relaxed">
            <li>— A pick-selling service.</li>
            <li>— A tout account.</li>
            <li>— A guaranteed-winners pitch.</li>
            <li>— A chase the parlay account.</li>
            <li>— Going to tell you a single bet will change your life.</li>
          </ul>
        </div>
        <div className="border border-conviction-elite/40 bg-conviction-eliteSoft/40 rounded-sm p-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-elite">
            We are
          </div>
          <ul className="mt-4 space-y-3 text-edge-textDim leading-relaxed">
            <li>— A transparent data resource.</li>
            <li>— An education platform for serious bettors.</li>
            <li>— A model that publishes its own report card.</li>
            <li>— Trying to make the &lsquo;why&rsquo; legible.</li>
            <li>— Here to make you better at this, not dependent on us.</li>
          </ul>
        </div>
      </section>

      {/* CTA */}
      <section className="mt-24 border-t border-edge-line pt-16">
        <h2 className="font-display text-4xl sm:text-5xl tracking-tightest leading-tight max-w-prose">
          That&apos;s the story.
          <br />
          <span className="italic text-edge-accent">Here&apos;s today&apos;s board.</span>
        </h2>
        <div className="mt-8 flex flex-wrap gap-4">
          <Link
            href="/daily-edge"
            className="inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-text transition-colors"
          >
            See Today&apos;s Edge →
          </Link>
          <Link
            href="/engine"
            className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
          >
            How The Engine Thinks
          </Link>
        </div>
      </section>
    </Layout>
  );
}

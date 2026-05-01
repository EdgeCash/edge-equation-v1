import Link from "next/link";
import type { GetServerSideProps } from "next";

import Layout from "@/components/Layout";
import ConvictionBadge from "@/components/ConvictionBadge";
import ConvictionKey from "@/components/ConvictionKey";
import MathBackdrop from "@/components/MathBackdrop";
import { api, formatAmericanOdds, formatDate, formatPercent } from "@/lib/api";
import {
  CONVICTION,
  tierFromGrade,
  type ConvictionTier,
} from "@/lib/conviction";
import type { ArchivedPick, SlateDetail } from "@/lib/types";

type TeaserPick = ArchivedPick & { _tier: ConvictionTier };

type Props = {
  generatedAt: string | null;
  totalPicks: number;
  teaser: TeaserPick[];
};

const TIER_RANK: Record<ConvictionTier, number> = {
  ELITE: 0,
  STRONG_NRFI: 1,
  STRONG_YRFI: 1,
  STRONG: 2,
  MODERATE: 3,
  LEAN: 4,
  NO_PLAY: 5,
};

function buildTeaser(slate: SlateDetail): TeaserPick[] {
  const enriched = slate.picks.map<TeaserPick>((p) => ({
    ...p,
    _tier: tierFromGrade(p.grade),
  }));
  enriched.sort((a, b) => {
    const r = TIER_RANK[a._tier] - TIER_RANK[b._tier];
    if (r !== 0) return r;
    const ea = a.edge ? Number(a.edge) : -Infinity;
    const eb = b.edge ? Number(b.edge) : -Infinity;
    return eb - ea;
  });
  // Top 3-5: prefer 5 if there are at least 5 picks above LEAN, otherwise 3.
  const above = enriched.filter((p) => TIER_RANK[p._tier] <= 2);
  const count = above.length >= 5 ? 5 : above.length >= 3 ? above.length : Math.min(3, enriched.length);
  return enriched.slice(0, count);
}

export const getServerSideProps: GetServerSideProps<Props> = async () => {
  try {
    const slate = await api.latestSlate("daily_edge");
    if (!slate) {
      return { props: { generatedAt: null, totalPicks: 0, teaser: [] } };
    }
    return {
      props: {
        generatedAt: slate.generated_at ?? null,
        totalPicks: slate.picks.length,
        teaser: buildTeaser(slate),
      },
    };
  } catch {
    return { props: { generatedAt: null, totalPicks: 0, teaser: [] } };
  }
};

function TeaserPickCard({ pick }: { pick: TeaserPick }) {
  const meta = CONVICTION[pick._tier];
  const isElite = pick._tier === "ELITE";
  const lineText = pick.line.number
    ? `${pick.line.number} @ ${formatAmericanOdds(pick.line.odds)}`
    : formatAmericanOdds(pick.line.odds);

  return (
    <div
      className={[
        "relative rounded-sm border bg-ink-900/70 backdrop-blur p-5 transition-colors",
        meta.borderClass,
        isElite ? "shadow-elite-glow" : "",
      ].join(" ")}
    >
      {isElite && (
        <div className="absolute -top-2 left-4 px-2 py-0.5 bg-ink-950 border border-conviction-elite text-conviction-elite font-mono text-[9px] uppercase tracking-[0.25em] rounded-sm">
          Elite
        </div>
      )}
      <div className="flex items-center justify-between gap-3 mb-3">
        <ConvictionBadge tier={pick._tier} />
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
          {pick.sport} · {pick.market_type}
        </span>
      </div>
      <div className="font-display text-xl tracking-tightest text-edge-text leading-snug">
        {pick.selection}
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Line</div>
          <div className="mt-1 font-mono tabular-nums text-edge-text">{lineText}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Fair</div>
          <div className="mt-1 font-mono tabular-nums text-edge-text">
            {formatPercent(pick.fair_prob, 1)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Edge</div>
          <div className={"mt-1 font-mono tabular-nums " + (isElite ? "text-conviction-elite" : "text-edge-text")}>
            {formatPercent(pick.edge, 1)}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Home({ generatedAt, totalPicks, teaser }: Props) {
  const hasTeaser = teaser.length > 0;

  return (
    <Layout>
      {/* Hero — cold-traffic optimized. Product clarity first
          ("Free Daily MLB Picks · Highest Conviction First"), brand
          tagline second ("Facts. Not Feelings."), CTAs third.
          The MathBackdrop adds the chalkboard / sigma / curve / candle
          decoration matching the brand graphics. */}
      <section className="relative pt-6 sm:pt-10 pb-2">
        <MathBackdrop variant="hero" />
        <div className="relative">
          <div className="eyebrow mb-5">
            MLB · 2026 Public Testing
          </div>
          <h1 className="font-display font-light text-[clamp(2.5rem,8vw,6rem)] leading-[0.96] tracking-tightest">
            Free Daily MLB Picks.
            <br />
            <span className="italic text-edge-accent">Highest Conviction First.</span>
          </h1>
          <p className="mt-6 max-w-prose text-edge-textDim text-base sm:text-lg leading-relaxed">
            Electric Blue plays + the full board + a transparent track record.
            <span className="mt-2 block font-mono text-[11px] uppercase tracking-[0.24em] text-edge-textFaint">
              Facts. Not Feelings.
            </span>
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3 sm:gap-4">
            <Link
              href="/daily-edge"
              className="group inline-flex items-center gap-3 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs font-semibold uppercase tracking-[0.22em] hover:bg-edge-text transition-all glow-cyan-soft"
            >
              See Today&apos;s Full Free Board
              <span className="group-hover:translate-x-1 transition-transform">→</span>
            </Link>
            <Link
              href="/track-record"
              className="group inline-flex items-center gap-3 border border-edge-accent text-edge-accent px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-accent hover:text-ink-950 transition-colors"
            >
              View Full Track Record
              <span className="group-hover:translate-x-1 transition-transform">→</span>
            </Link>
            <span className="inline-flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.22em] text-conviction-elite">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-conviction-elite" aria-hidden="true" />
              Free. Always.
            </span>
          </div>
        </div>
      </section>

      {/* Electric Blue board — pulled close to the hero so the highest-
          conviction picks land above the fold for cold X traffic. */}
      <section className="mt-12 sm:mt-14">
        <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
          <div>
            <div className="eyebrow mb-2">
              Today&apos;s Highest Conviction
            </div>
            <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
              The Electric Blue board
            </h2>
          </div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
            {generatedAt ? `Slate · ${formatDate(generatedAt)}` : "Awaiting today’s slate"}
            {totalPicks > 0 && ` · ${totalPicks} total picks`}
          </div>
        </div>

        {hasTeaser ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {teaser.map((p) => (
              <TeaserPickCard key={`${p.pick_id}`} pick={p} />
            ))}
          </div>
        ) : (
          <div className="rounded-sm border border-edge-line bg-ink-900/60 p-8">
            <p className="text-edge-textDim max-w-prose">
              No slate has been published yet today. While we wait for confirmed
              lineups + weather + umpires, the past picks and running record
              live on the public ledger.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-4">
              <Link
                href="/track-record"
                className="font-mono text-xs uppercase tracking-[0.22em] text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent pb-1"
              >
                See full track record →
              </Link>
              <Link
                href="/archive"
                className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
              >
                Browse archive
              </Link>
            </div>
          </div>
        )}

        <div className="mt-6 flex flex-wrap items-center gap-4">
          <Link
            href="/daily-edge"
            className="font-mono text-xs uppercase tracking-[0.22em] text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent pb-1"
          >
            See the full slate →
          </Link>
          <Link
            href="/track-record"
            className="font-mono text-xs uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1"
          >
            View track record →
          </Link>
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
            Free. Always.
          </span>
        </div>
      </section>

      {/* Conviction Key */}
      <section className="mt-24">
        <div className="grid gap-8 md:grid-cols-[1fr_1.4fr] items-start">
          <div>
            <div className="eyebrow mb-3">The Conviction System</div>
            <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight">
              One scale. Both sides.
            </h2>
            <p className="mt-4 text-edge-textDim leading-relaxed max-w-prose">
              Every pick we publish is tagged with a single tier. Electric Blue
              is the top of the ladder, on either side of a market — a strong
              NRFI lean and a strong YRFI lean can both reach Electric Blue
              when the math gets there. The colours don&apos;t change. The
              standards don&apos;t change.
            </p>
            <Link
              href="/conviction"
              className="mt-6 inline-block font-mono text-xs uppercase tracking-[0.22em] text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent pb-1"
            >
              How conviction works →
            </Link>
          </div>
          <ConvictionKey />
        </div>
      </section>

      {/* Our Story blurb */}
      <section className="mt-24">
        <div className="border border-edge-line rounded-sm bg-ink-900/60 p-8 sm:p-12">
          <div className="grid gap-8 md:grid-cols-[180px_1fr] items-start">
            <div className="eyebrow-dim">
              Our Story · V1 → V4
            </div>
            <div>
              <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-tight max-w-prose">
                Built as a tool for one bettor. Opened up because the{" "}
                <span className="italic text-edge-accent">process</span> mattered more than the picks.
              </h2>
              <p className="mt-5 text-edge-textDim leading-relaxed max-w-prose">
                Edge Equation started as a personal model — a way to keep
                ourselves honest about what we actually believed versus what we
                felt. Three iterations later, we realized the numbers were the
                least interesting part. The process — the reasoning, the
                discipline, the willingness to say{" "}
                <em className="text-edge-text not-italic">no play</em> — was
                what actually moved the needle. V4 is what happens when we make
                that process public.
              </p>
              <Link
                href="/about"
                className="mt-6 inline-block font-mono text-xs uppercase tracking-[0.22em] text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent pb-1"
              >
                Read the full story →
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Value prop strip — three doors. Track Record replaces the
          old Premium tile during the free public-testing phase. */}
      <section className="mt-24 grid grid-cols-1 md:grid-cols-3 gap-px bg-edge-line">
        {[
          {
            num: "01",
            title: "Free Daily Board",
            body:
              "The Electric Blue board, every day. No paywall. No upsell to see today’s call.",
            href: "/daily-edge",
            cta: "Today’s board",
          },
          {
            num: "02",
            title: "Transparent Track Record",
            body:
              "Every LEAN-and-above pick we’ve ever published — wins, losses, pushes — logged honestly. The receipts live on a public page.",
            href: "/track-record",
            cta: "View track record",
          },
          {
            num: "03",
            title: "Learn The Craft",
            body:
              "Bankroll, Kelly, variance, line shopping. The fundamentals nobody on Twitter posts.",
            href: "/learn",
            cta: "Start learning",
          },
        ].map((p) => (
          <div key={p.num} className="bg-ink-950 p-8 flex flex-col">
            <div className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
              {p.num}
            </div>
            <h3 className="mt-4 font-display text-2xl tracking-tightest">{p.title}</h3>
            <p className="mt-3 text-edge-textDim flex-1">{p.body}</p>
            <Link
              href={p.href}
              className="mt-5 font-mono text-[11px] uppercase tracking-[0.22em] text-edge-textDim hover:text-edge-accent border-b border-transparent hover:border-edge-accent pb-1 self-start"
            >
              {p.cta} →
            </Link>
          </div>
        ))}
      </section>

      {/* Disclaimer */}
      <section className="mt-20">
        <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint max-w-prose leading-relaxed">
          Edge Equation publishes data and analysis. We do not sell guaranteed
          winners — they don&apos;t exist. Past performance does not predict
          future results. Bet within your means. 21+. If you or someone you
          know has a gambling problem, call{" "}
          <a
            href="tel:18004262537"
            className="text-edge-textDim underline underline-offset-2"
          >
            1-800-GAMBLER
          </a>
          .
        </p>
      </section>
    </Layout>
  );
}

import Link from "next/link";
import type { GetServerSideProps } from "next";

import CardShell from "@/components/CardShell";
import ConvictionBadge from "@/components/ConvictionBadge";
import ConvictionKey from "@/components/ConvictionKey";
import Layout from "@/components/Layout";
import MathBackdrop from "@/components/MathBackdrop";
import StatTile from "@/components/StatTile";
import { formatAmericanOdds, formatDate, formatPercent } from "@/lib/api";
import { tierFromGrade, type ConvictionTier } from "@/lib/conviction";
import { loadDailyView, type DailySlateView } from "@/lib/daily-feed";
import type { ArchivedPick } from "@/lib/types";


type Props = {
  view: DailySlateView | null;
  error: string | null;
};


type GroupKey = "first_inning" | "props" | "full_game" | "other";

type Group = {
  key: GroupKey;
  label: string;
  description: string;
  picks: ArchivedPick[];
};


export const getServerSideProps: GetServerSideProps<Props> = async () => {
  const { view, error } = await loadDailyView();
  return { props: { view, error } };
};


// Map a market_type string into a high-level group. The engine emits values
// like "NRFI", "YRFI", "PLAYER_HITS", "MONEYLINE", "TOTAL", etc. — group them
// for the unified daily report.
function classify(market: string): GroupKey {
  const m = (market || "").toUpperCase();
  if (m === "NRFI" || m === "YRFI" || m.includes("FIRST_INNING")) {
    return "first_inning";
  }
  if (
    m.includes("PLAYER") ||
    m.includes("PROP") ||
    m.includes("HITS") ||
    m.includes("HRS") ||
    m.includes("STRIKEOUT") ||
    m.includes("RBI")
  ) {
    return "props";
  }
  if (
    m === "MONEYLINE" ||
    m === "TOTAL" ||
    m === "RUN_LINE" ||
    m === "SPREAD" ||
    m.includes("FULL_GAME")
  ) {
    return "full_game";
  }
  return "other";
}


const TIER_RANK: Record<ConvictionTier, number> = {
  ELITE: 0,
  STRONG: 1,
  MODERATE: 2,
  LEAN: 3,
  NO_PLAY: 4,
};


function bucket(picks: ArchivedPick[]): Group[] {
  const groups: Record<GroupKey, ArchivedPick[]> = {
    first_inning: [],
    props: [],
    full_game: [],
    other: [],
  };
  for (const p of picks) {
    groups[classify(p.market_type)].push(p);
  }
  for (const k of Object.keys(groups) as GroupKey[]) {
    groups[k].sort((a, b) => {
      const ta = TIER_RANK[tierFromGrade(a.grade)];
      const tb = TIER_RANK[tierFromGrade(b.grade)];
      if (ta !== tb) return ta - tb;
      const ea = a.edge ? Number(a.edge) : -Infinity;
      const eb = b.edge ? Number(b.edge) : -Infinity;
      return eb - ea;
    });
  }
  const out: Group[] = [
    {
      key: "first_inning",
      label: "First Inning",
      description: "NRFI / YRFI reads. Deep Green is the NRFI side, Red is the YRFI side.",
      picks: groups.first_inning,
    },
    {
      key: "props",
      label: "Props",
      description: "Player and team props priced against current market lines.",
      picks: groups.props,
    },
    {
      key: "full_game",
      label: "Full Game",
      description: "Moneylines, totals, and run lines on the full nine.",
      picks: groups.full_game,
    },
  ];
  if (groups.other.length > 0) {
    out.push({
      key: "other",
      label: "Other Markets",
      description: "Anything that doesn't fit the buckets above.",
      picks: groups.other,
    });
  }
  return out.filter((g) => g.picks.length > 0);
}


function topEdge(picks: ArchivedPick[]): string | null {
  let best: number | null = null;
  for (const p of picks) {
    if (!p.edge) continue;
    const n = Number(p.edge);
    if (Number.isNaN(n)) continue;
    if (best == null || n > best) best = n;
  }
  return best == null ? null : String(best);
}


function eliteCount(picks: ArchivedPick[]): number {
  return picks.filter((p) => tierFromGrade(p.grade) === "ELITE").length;
}


// -----------------------------------------------------------------------------
// Simplified pick row — clean, scannable, conviction-first.
// -----------------------------------------------------------------------------

function CleanPickRow({ pick }: { pick: ArchivedPick }) {
  const tier = tierFromGrade(pick.grade);
  const isElite = tier === "ELITE";
  const lineText = pick.line.number
    ? `${pick.line.number} @ ${formatAmericanOdds(pick.line.odds)}`
    : formatAmericanOdds(pick.line.odds);
  return (
    <div
      className={
        "rounded-sm border bg-ink-900/40 px-5 py-4 grid grid-cols-12 gap-4 items-center " +
        (isElite ? "border-conviction-elite shadow-elite-glow" : "border-edge-line")
      }
    >
      <div className="col-span-12 sm:col-span-3 flex items-center gap-3">
        <ConvictionBadge tier={tier} />
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim">
          {pick.sport}
        </span>
      </div>
      <div className="col-span-12 sm:col-span-5">
        <div className="font-display text-lg tracking-tightest text-edge-text leading-tight">
          {pick.selection}
        </div>
        <div className="mt-1 font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textFaint">
          {pick.market_type} · {lineText}
        </div>
      </div>
      <div className="col-span-6 sm:col-span-2">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Fair</div>
        <div className="mt-1 font-mono tabular-nums text-edge-text">
          {formatPercent(pick.fair_prob, 1)}
        </div>
      </div>
      <div className="col-span-6 sm:col-span-2">
        <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Edge</div>
        <div className={"mt-1 font-mono tabular-nums " + (isElite ? "text-conviction-elite" : "text-edge-text")}>
          {formatPercent(pick.edge, 1)}
        </div>
      </div>
    </div>
  );
}


function TestingDisclaimer({ placement }: { placement: "top" | "bottom" }) {
  return (
    <aside
      className={
        "rounded-sm border border-conviction-elite/30 bg-conviction-eliteSoft/30 p-4 sm:p-5 " +
        (placement === "top" ? "mt-8" : "mt-12")
      }
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-elite mb-2">
        Public Testing Phase · v1
      </div>
      <p className="text-sm text-edge-textDim leading-relaxed max-w-prose">
        Results are being tracked transparently. These are data projections only —
        not betting advice. Bet responsibly. 21+.
      </p>
    </aside>
  );
}


export default function DailyEdge({ view, error }: Props) {
  const groups = view ? bucket(view.picks) : [];
  const sourceLabel =
    view?.source === "todays_card.csv"
      ? "exporters.mlb.daily_spreadsheet · cron"
      : view?.source
      ? `${view.source} · static feed`
      : "no source";

  return (
    <Layout
      title="Daily Edge"
      description="Today's unified board — First Inning, Props, and Full Game — from the Edge Equation engine. Free, every day."
    >
      <section className="relative mb-2 pb-2">
        <MathBackdrop variant="section" />
        <div className="relative">
          <div className="eyebrow mb-4">Free · Every Day</div>
          <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
            Daily <span className="italic text-edge-accent">Edge.</span>
          </h1>
          <p className="mt-6 max-w-prose text-edge-textDim text-lg leading-relaxed">
            One unified board, grouped by market. Every pick carries a single
            conviction tier — Electric Blue is the top of the ladder, on either
            side. Most days, most plays sit below it. That&apos;s the point.
          </p>
        </div>
      </section>

      <TestingDisclaimer placement="top" />

      {error && !view && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.22em] text-[10px] mb-2">
            Data Error
          </div>
          <p className="text-edge-text font-mono text-sm">{error}</p>
          <p className="mt-3 text-edge-textDim text-sm">
            Daily Edge tried{" "}
            <code className="font-mono">website/public/data/mlb/todays_card.csv</code>.
            The daily MLB cron writes that file at 11 AM ET.
          </p>
        </div>
      )}

      {!view && !error && (
        <div className="mt-10">
          <CardShell
            eyebrow="Awaiting today’s slate"
            headline="No daily slate has been published yet."
            subhead="Once the MLB Daily Spreadsheet workflow writes website/public/data/mlb/todays_card.csv, today's picks will appear here automatically."
          >
            <p className="text-edge-textDim">
              Follow on{" "}
              <Link
                href="https://x.com/edgeequation"
                className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
              >
                X
              </Link>{" "}
              for posted cards.
            </p>
          </CardShell>
        </div>
      )}

      {view && (
        <div className="mt-10 space-y-12">
          {/* Slate summary */}
          <CardShell
            eyebrow={`Slate · ${formatDate(view.generatedAt)}`}
            headline={`${view.picks.length} pick${view.picks.length === 1 ? "" : "s"} on the unified board`}
            subhead={`Source: ${sourceLabel}${view.date ? ` · ${view.date}` : ""}`}
          >
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile label="Top Edge" value={formatPercent(topEdge(view.picks))} />
              <StatTile label="Electric Blue" value={String(eliteCount(view.picks))} />
              <StatTile
                label="First Inning"
                value={String(groups.find((g) => g.key === "first_inning")?.picks.length ?? 0)}
              />
              <StatTile
                label="Props"
                value={String(groups.find((g) => g.key === "props")?.picks.length ?? 0)}
              />
            </div>
            {view.notes && (
              <p className="mt-5 text-edge-textDim text-sm leading-relaxed border-t border-edge-line pt-4">
                {view.notes}
              </p>
            )}
          </CardShell>

          {/* Grouped sections */}
          {groups.map((g) => (
            <section key={g.key}>
              <div className="flex flex-wrap items-end justify-between gap-3 mb-3">
                <div>
                  <div className="eyebrow mb-1">{g.label}</div>
                  <h2 className="font-display text-2xl sm:text-3xl tracking-tightest leading-tight">
                    {g.picks.length} pick{g.picks.length === 1 ? "" : "s"}
                  </h2>
                </div>
                <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-textFaint max-w-md text-right">
                  {g.description}
                </div>
              </div>
              <div className="space-y-3">
                {g.picks.map((p) => (
                  <CleanPickRow key={p.pick_id} pick={p} />
                ))}
              </div>
            </section>
          ))}

          <section>
            <div className="eyebrow mb-3">The Key</div>
            <ConvictionKey />
          </section>
        </div>
      )}

      <TestingDisclaimer placement="bottom" />

      <p className="mt-10 text-edge-textDim">
        Want the full Why notes, deeper analysis, and parlay reasoning?{" "}
        <Link
          href="/premium-edge"
          className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
        >
          Explore Premium
        </Link>
        . Or follow on{" "}
        <Link
          href="https://x.com/edgeequation"
          className="text-edge-accent border-b border-edge-accent/40 hover:border-edge-accent"
        >
          X
        </Link>
        {" "}for live cards.
      </p>
    </Layout>
  );
}

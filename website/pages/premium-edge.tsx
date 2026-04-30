import type { GetServerSideProps } from "next";
import Link from "next/link";

import CardShell from "@/components/CardShell";
import ConvictionBadge from "@/components/ConvictionBadge";
import Layout from "@/components/Layout";
import StatTile from "@/components/StatTile";
import { api, apiBase, formatAmericanOdds, formatNumber, formatPercent } from "@/lib/api";
import { tierFromGrade } from "@/lib/conviction";
import type { MeResponse } from "@/lib/types";


const FEATURES = [
  {
    title: "Full Distributions",
    body:
      "p10 / p50 / p90 and the modeled mean from deterministic Monte Carlo. See the full shape of the outcome, not just the headline number.",
  },
  {
    title: "The Why Notes",
    body:
      "A short paragraph on every pick explaining what drove the number, what the model is leaning on, and what would change the read.",
  },
  {
    title: "Conviction & Sizing",
    body:
      "The same conviction tiers as the public board, with explicit half-Kelly sizing on every play. Math, not vibes.",
  },
  {
    title: "Slate Walkthroughs",
    body:
      "A written breakdown of the day’s slate — including the games we passed on and why. Process, not just picks.",
  },
];


type PremiumPick = {
  selection: string;
  market_type: string;
  sport: string;
  line_odds: number | null;
  line_number: string | null;
  fair_prob: string | null;
  expected_value: string | null;
  edge: string | null;
  grade: string;
  kelly: string | null;
  realization: number;
  game_id: string | null;
  event_time: string | null;
  p10: string | null;
  p50: string | null;
  p90: string | null;
  mean: string | null;
  notes: string | null;
};


type Props = {
  me: MeResponse | null;
  picks: PremiumPick[];
  picksError: string | null;
  meError: string | null;
};


export const getServerSideProps: GetServerSideProps<Props> = async (ctx) => {
  const cookie = ctx.req.headers.cookie;
  let me: MeResponse | null = null;
  let meError: string | null = null;
  try {
    me = await api.me(cookie);
  } catch (e: unknown) {
    meError = e instanceof Error ? e.message : "unknown error";
  }

  let picks: PremiumPick[] = [];
  let picksError: string | null = null;

  if (me?.has_active_subscription) {
    try {
      const resp = await fetch(`${apiBase()}/premium/picks/today`, {
        headers: cookie ? { Cookie: cookie, Accept: "application/json" } : { Accept: "application/json" },
        cache: "no-store",
      });
      if (resp.ok) picks = await resp.json();
      else picksError = `HTTP ${resp.status}`;
    } catch (e) {
      picksError = e instanceof Error ? e.message : "fetch error";
    }
  }

  return { props: { me, picks, picksError, meError } };
};


function PremiumPickCard({ pick }: { pick: PremiumPick }) {
  const tier = tierFromGrade(pick.grade);
  const lineText = pick.line_number
    ? `${pick.line_number} @ ${formatAmericanOdds(pick.line_odds)}`
    : formatAmericanOdds(pick.line_odds);
  return (
    <div className="border border-edge-line rounded-sm p-5 bg-ink-900/60">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <ConvictionBadge tier={tier} />
          <span className="font-mono text-[11px] uppercase tracking-[0.22em] text-edge-textDim">
            {pick.sport} · {pick.market_type}
          </span>
        </div>
        <span className="font-mono tabular-nums text-edge-textDim text-sm">{lineText}</span>
      </div>
      <div className="font-display text-xl tracking-tightest text-edge-text">
        {pick.selection}
      </div>
      <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Fair / EV</div>
          <div className="font-mono tabular-nums text-edge-text">
            {pick.fair_prob ? formatPercent(pick.fair_prob) : formatNumber(pick.expected_value, 2)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Edge · ½ Kelly</div>
          <div className="font-mono tabular-nums text-edge-text">
            {formatPercent(pick.edge)} · {formatPercent(pick.kelly)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">p10 / p50 / p90</div>
          <div className="font-mono tabular-nums text-edge-text">
            {formatNumber(pick.p10, 2)} · {formatNumber(pick.p50, 2)} · {formatNumber(pick.p90, 2)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">Mean</div>
          <div className="font-mono tabular-nums text-edge-text">
            {formatNumber(pick.mean, 2)}
          </div>
        </div>
      </div>
      {pick.notes && (
        <div className="mt-4 border-t border-edge-line pt-3">
          <div className="text-[10px] uppercase tracking-[0.22em] text-edge-accent mb-2">
            The Why
          </div>
          <p className="text-sm text-edge-textDim leading-relaxed">{pick.notes}</p>
        </div>
      )}
    </div>
  );
}


function Paywall({ me }: { me: MeResponse | null }) {
  return (
    <>
      <div className="mt-12 grid grid-cols-1 md:grid-cols-2 gap-px bg-edge-line">
        {FEATURES.map((f, i) => (
          <div key={f.title} className="bg-ink-900 p-8">
            <div className="flex items-baseline gap-3 mb-3">
              <span className="font-mono text-[10px] tracking-[0.3em] text-edge-accent">
                {String(i + 1).padStart(2, "0")}
              </span>
              <h3 className="font-display text-2xl tracking-tightest">{f.title}</h3>
            </div>
            <p className="text-edge-textDim leading-relaxed">{f.body}</p>
          </div>
        ))}
      </div>

      {/* What Premium is and isn't */}
      <div className="mt-12 grid gap-6 md:grid-cols-2">
        <div className="border border-conviction-elite/40 bg-conviction-eliteSoft/40 rounded-sm p-7">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-elite mb-3">
            What Premium is
          </div>
          <ul className="space-y-2 text-edge-textDim leading-relaxed text-sm">
            <li>— A deeper look at the same engine.</li>
            <li>— Distributions, sizing, and full Why notes.</li>
            <li>— Slate walkthroughs explaining the passes.</li>
            <li>— A way to support free education for everyone else.</li>
          </ul>
        </div>
        <div className="border border-conviction-fade/40 bg-conviction-fadeSoft/40 rounded-sm p-7">
          <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-fade mb-3">
            What Premium isn&apos;t
          </div>
          <ul className="space-y-2 text-edge-textDim leading-relaxed text-sm">
            <li>— A secret pick we hold back from the public board.</li>
            <li>— A guarantee of profit. Nothing on this site is.</li>
            <li>— A replacement for your own bankroll discipline.</li>
            <li>— A subscription you should buy if you can&apos;t afford to lose it.</li>
          </ul>
        </div>
      </div>

      <div className="mt-12">
        <CardShell eyebrow="Premium" headline="Subscribe to unlock the deeper look">
          <p className="text-edge-textDim leading-relaxed">
            Premium is built for people who want to study the engine, not for
            people looking for a hot tip. Cancel anytime via Stripe&apos;s
            customer portal. We&apos;d rather you cancel than overspend.
          </p>
          <div className="mt-6">
            {me == null ? (
              <Link
                href="/login"
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-text transition-colors"
              >
                Sign in to subscribe →
              </Link>
            ) : (
              <Link
                href="/account"
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.22em] hover:bg-edge-text transition-colors"
              >
                Start premium subscription →
              </Link>
            )}
          </div>
        </CardShell>
      </div>
    </>
  );
}


function StrongDisclaimer() {
  return (
    <aside className="mt-16 border-l-2 border-conviction-fade bg-conviction-fadeSoft/30 pl-6 py-5 pr-5">
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-conviction-fade mb-3">
        Read this before subscribing
      </div>
      <div className="text-edge-textDim leading-relaxed max-w-prose space-y-3 text-sm">
        <p>
          <strong className="text-edge-text">There are no guaranteed winners.</strong>{" "}
          Anyone who tells you otherwise is selling you something. Edge
          Equation publishes a model. Models are wrong. Frequently. The
          question is whether the long-run record beats break-even at the
          conviction tiers you bet at — not whether last Tuesday hit.
        </p>
        <p>
          Sports betting carries real risk of real loss. Past performance,
          including ours, does not predict future results. The conviction
          tiers, the Kelly sizing, and the educational content on this site
          are tools — they are not a license to bet money you can&apos;t
          afford to lose.
        </p>
        <p>
          If you or someone you know has a gambling problem, call{" "}
          <span className="text-edge-text">1-800-GAMBLER</span>. You must be
          21+ to use this product in jurisdictions where it is legal. If
          it&apos;s not legal where you are, this content is for educational
          and entertainment use only.
        </p>
      </div>
    </aside>
  );
}


export default function PremiumEdge({ me, picks, picksError, meError }: Props) {
  const subscribed = me?.has_active_subscription === true;

  return (
    <Layout
      title="Premium"
      description="Distributions, full Why notes, and slate walkthroughs from the same engine that powers the free daily board."
    >
      <div className="eyebrow mb-4">
        {subscribed ? "Subscriber · Unlocked" : "Premium"}
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-[0.95]">
        The deeper look at the{" "}
        <span className="italic text-edge-accent">same engine.</span>
      </h1>
      <p className="mt-6 max-w-prose text-edge-textDim text-lg leading-relaxed">
        Premium is the full data layer for people who want to study the model
        instead of just see today&apos;s call. Distributions, sizing, full Why
        notes, and a written walkthrough of the slate. The free board is still
        the free board — Premium just shows you how the call was built.
      </p>

      {meError && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.22em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{meError}</p>
        </div>
      )}

      {!subscribed && (
        <>
          <Paywall me={me} />
          <StrongDisclaimer />
        </>
      )}

      {subscribed && (
        <div className="mt-10 space-y-8">
          <CardShell eyebrow="Today" headline={`${picks.length} premium pick${picks.length === 1 ? "" : "s"}`}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile
                label="With Kelly"
                value={String(picks.filter((p) => p.kelly && Number(p.kelly) > 0).length)}
              />
              <StatTile
                label="Elite"
                value={String(picks.filter((p) => p.grade === "A+").length)}
              />
              <StatTile
                label="Sports"
                value={String(new Set(picks.map((p) => p.sport)).size)}
              />
              <StatTile label="Pick count" value={String(picks.length)} />
            </div>
          </CardShell>

          {picksError && (
            <div className="border border-edge-line rounded-sm p-4">
              <p className="font-mono text-sm text-edge-accent">
                Error fetching premium picks: {picksError}
              </p>
            </div>
          )}

          <section>
            <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-accent mb-4">
              Every premium pick
            </div>
            <div className="space-y-3">
              {picks.map((p, i) => (
                <PremiumPickCard key={`${p.game_id}-${p.market_type}-${i}`} pick={p} />
              ))}
              {picks.length === 0 && (
                <p className="text-edge-textDim">
                  No premium picks in the current slate.
                </p>
              )}
            </div>
          </section>

          <StrongDisclaimer />
        </div>
      )}
    </Layout>
  );
}

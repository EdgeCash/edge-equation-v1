import type { GetServerSideProps } from "next";
import Link from "next/link";

import CardShell from "@/components/CardShell";
import GradeBadge from "@/components/GradeBadge";
import Layout from "@/components/Layout";
import StatTile from "@/components/StatTile";
import { api, apiBase, formatAmericanOdds, formatNumber, formatPercent } from "@/lib/api";
import type { MeResponse } from "@/lib/types";


const FEATURES = [
  { title: "Full Distributions", body: "p10 / p50 / p90 and mean from deterministic Monte Carlo simulation." },
  { title: "Letter Grades", body: "A+ / A / B / C ratings on every pick, calibrated to realization buckets." },
  { title: "Kelly Guidance", body: "Half-Kelly, 25% cap, gated at meaningful edge. Sizing built in." },
  { title: "Model Notes", body: "Context on what drove the number and what would change it." },
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

  // Only fetch picks if we have an active subscription (the API will 403
  // the unsubscribed; the paywall render handles that case instead).
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
  const lineText = pick.line_number
    ? `${pick.line_number} @ ${formatAmericanOdds(pick.line_odds)}`
    : formatAmericanOdds(pick.line_odds);
  return (
    <div className="border border-edge-line rounded-sm p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <GradeBadge grade={pick.grade} />
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textDim">
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
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">Fair / EV</div>
          <div className="font-mono tabular-nums text-edge-text">
            {pick.fair_prob ? formatPercent(pick.fair_prob) : formatNumber(pick.expected_value, 2)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">Edge · ½ Kelly</div>
          <div className="font-mono tabular-nums text-edge-text">
            {formatPercent(pick.edge)} · {formatPercent(pick.kelly)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">p10 / p50 / p90</div>
          <div className="font-mono tabular-nums text-edge-text">
            {formatNumber(pick.p10, 2)} · {formatNumber(pick.p50, 2)} · {formatNumber(pick.p90, 2)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">Mean</div>
          <div className="font-mono tabular-nums text-edge-text">
            {formatNumber(pick.mean, 2)}
          </div>
        </div>
      </div>
      {pick.notes && (
        <p className="mt-3 text-sm text-edge-textDim">{pick.notes}</p>
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
            <p className="text-edge-textDim">{f.body}</p>
          </div>
        ))}
      </div>

      <div className="mt-12">
        <CardShell eyebrow="Premium" headline="Subscribe to unlock">
          <p className="text-edge-textDim">
            Premium picks carry full distributions, grades, sizing, and model
            notes. Cancel anytime via Stripe&apos;s customer portal.
          </p>
          <div className="mt-5">
            {me == null ? (
              <Link
                href="/login"
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-text transition-colors"
              >
                Sign in to subscribe →
              </Link>
            ) : (
              <Link
                href="/account"
                className="inline-flex items-center gap-2 bg-edge-accent text-ink-950 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] hover:bg-edge-text transition-colors"
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


export default function PremiumEdge({ me, picks, picksError, meError }: Props) {
  const subscribed = me?.has_active_subscription === true;

  return (
    <Layout
      title="Premium Edge"
      description="Full Monte Carlo distributions, grades, sizing, and model notes."
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent mb-4">
        {subscribed ? "Subscriber · Unlocked" : "Premium"}
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Premium <span className="italic text-edge-accent">Edge</span>
      </h1>
      <p className="mt-4 text-edge-textDim max-w-prose">
        Distributions, grades, sizing, and model notes. The same engine that
        powers the public card, with everything unredacted.
      </p>

      {meError && (
        <div className="mt-10 border border-edge-line rounded-sm p-6 bg-ink-900/80">
          <div className="text-edge-accent uppercase tracking-[0.2em] text-[10px] mb-2">
            API Error
          </div>
          <p className="text-edge-text font-mono text-sm">{meError}</p>
        </div>
      )}

      {!subscribed && <Paywall me={me} />}

      {subscribed && (
        <div className="mt-10 space-y-8">
          <CardShell eyebrow="Today" headline={`${picks.length} premium pick${picks.length === 1 ? "" : "s"}`}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatTile
                label="With Kelly"
                value={String(picks.filter((p) => p.kelly && Number(p.kelly) > 0).length)}
              />
              <StatTile
                label="A+ / A"
                value={String(picks.filter((p) => p.grade === "A" || p.grade === "A+").length)}
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
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-accent mb-4">
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
        </div>
      )}
    </Layout>
  );
}

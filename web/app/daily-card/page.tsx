import Link from "next/link";
import { headers } from "next/headers";
import { AlertBanner } from "../../components/AlertBanner";
import { ChalkboardBackground } from "../../components/ChalkboardBackground";
import { DailyCardView, SPORTS_DISPLAY } from "../../components/DailyCardView";
import { EmailSignup } from "../../components/EmailSignup";
import { loadAlertReport } from "../../lib/alerts";
import {
  getDailyFeed,
  picksForSport,
  gameParlaysForSport,
  propParlaysForSport,
  type FeedParlay,
  type FeedPick,
  type SportKey,
} from "../../lib/feed";
import { getDailyData } from "../../lib/types";

export const dynamic = "force-dynamic";


export const metadata = {
  title: "Today's daily card",
  description:
    "Today's full daily card across every sport we cover. ELITE and "
    + "STRONG by default; toggle for every LEAN+ pick the engine "
    + "graded today. Updated by 11 AM CDT.",
};


export default async function DailyCardPage() {
  const h = await headers();
  const host = h.get("host");
  const proto = h.get("x-forwarded-proto") ?? "https";
  const origin = host ? `${proto}://${host}` : undefined;

  const [feed, mlbDaily, alerts] = await Promise.all([
    getDailyFeed(),
    // Legacy MLB payload only feeds the hero (slate / priced / odds source).
    // Picks come from the unified feed below.
    getDailyData(origin),
    loadAlertReport(),
  ]);

  if (!feed) {
    return <DataUnavailable />;
  }

  const picksBySport: Record<SportKey, FeedPick[]> = {
    mlb: picksForSport(feed, "mlb"),
    wnba: picksForSport(feed, "wnba"),
    nfl: picksForSport(feed, "nfl"),
    ncaaf: picksForSport(feed, "ncaaf"),
  };
  const parlaysBySport: Record<
    SportKey, { game_results: FeedParlay[]; player_props: FeedParlay[] }
  > = {
    mlb: {
      game_results: gameParlaysForSport(feed, "mlb"),
      player_props: propParlaysForSport(feed, "mlb"),
    },
    wnba: {
      game_results: gameParlaysForSport(feed, "wnba"),
      player_props: propParlaysForSport(feed, "wnba"),
    },
    nfl: {
      game_results: gameParlaysForSport(feed, "nfl"),
      player_props: propParlaysForSport(feed, "nfl"),
    },
    ncaaf: {
      game_results: gameParlaysForSport(feed, "ncaaf"),
      player_props: propParlaysForSport(feed, "ncaaf"),
    },
  };

  const totalPicks = SPORTS_DISPLAY.reduce(
    (n, { key }) => n + picksBySport[key].length, 0,
  );
  const sportsWithPicks = SPORTS_DISPLAY.filter(
    ({ key }) => picksBySport[key].length > 0,
  ).map(({ label }) => label);

  const generatedAt = feed.generated_at;
  const today = feed.date;
  const oddsSource = mlbDaily?.odds_source ?? feed.source ?? "—";
  const slateGames = mlbDaily?.counts.slate_games;
  const pricedGames = mlbDaily?.counts.priced_games;

  return (
    <>
      <AlertBanner report={alerts} />
      <section className="relative overflow-hidden border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12 sm:py-16">
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Today
          </p>
          <h1 className="mt-1 text-4xl sm:text-5xl font-bold text-chalk-50">
            {today}
            <span className="block text-base font-normal text-chalk-300 mt-2">
              {totalPicks === 0
                ? "No plays today across any sport. The math says pass."
                : `${totalPicks} ${totalPicks === 1 ? "pick" : "picks"} on the card across ${sportsWithPicks.join(" / ")}.`}
            </span>
          </h1>

          <p className="mt-6 text-sm text-chalk-300 max-w-2xl">
            {slateGames !== undefined && pricedGames !== undefined ? (
              <>
                {slateGames} MLB games on the slate · {pricedGames} priced via{" "}
                <span className="text-chalk-100">{oddsSource}</span> ·{" "}
              </>
            ) : null}
            published{" "}
            <time dateTime={generatedAt}>
              {new Date(generatedAt).toLocaleString("en-US", {
                timeZone: "America/New_York",
                hour: "numeric",
                minute: "2-digit",
                month: "short",
                day: "numeric",
              })}{" "}
              ET
            </time>
          </p>
          <p className="mt-3 text-xs font-mono text-chalk-500 border-t border-chalkboard-700/60 pt-3">
            Picks shown only for games not yet started ·{" "}
            <UpcomingOnlyTimestamp generatedAt={generatedAt} />
          </p>
        </div>
      </section>

      {totalPicks > 0 ? (
        <DailyCardView
          picksBySport={picksBySport}
          parlaysBySport={parlaysBySport}
          generatedAt={generatedAt}
        />
      ) : (
        <section className="max-w-7xl mx-auto px-4 sm:px-6 py-10 sm:py-14">
          <EmptyCard />
        </section>
      )}

      <section className="max-w-7xl mx-auto px-4 sm:px-6 pb-10">
        <p className="text-xs text-chalk-500 max-w-2xl">
          Tier and unit count are derived from the model&apos;s edge over the
          current best market price. The default view shows ELITE and STRONG
          only; toggle to <em>All LEAN+</em> for every pick the engine graded
          today, or click <em>Download CSV</em> for an audit-trail export. CLV
          is captured at first pitch and shown on the{" "}
          <Link href="/track-record" className="text-elite hover:underline">
            track record
          </Link>
          .
        </p>
      </section>

      <section className="max-w-3xl mx-auto px-4 sm:px-6 pb-12">
        <EmailSignup
          headline="Want this in your inbox?"
          subline={
            "One daily card per day, before 11 AM CDT. "
            + "Same picks, same parlays, same data. Unsubscribe any time."
          }
        />
      </section>
    </>
  );
}

/* ---------- Empty card ---------- */

function EmptyCard() {
  return (
    <div className="chalk-card p-8 sm:p-12 text-center">
      <p className="font-chalk text-3xl text-elite/80 -rotate-2 inline-block">
        Some days, no play is the play.
      </p>
      <h2 className="mt-4 text-2xl font-bold text-chalk-50">
        Today&apos;s Card is intentionally empty.
      </h2>
      <p className="mt-3 text-chalk-300 max-w-2xl mx-auto leading-relaxed">
        Per our market gating rule, a market only ships when its rolling
        backtest sample shows ≥+1% ROI <em>and</em> Brier under 0.246. Today,
        none of our markets clear both bars. Volume without edge is how
        bankrolls die.
      </p>

      <div className="mt-8 flex flex-wrap justify-center gap-3">
        <Link href="/track-record" className="btn-ghost">
          See track record
        </Link>
        <Link href="/methodology" className="btn-ghost">
          Read the methodology
        </Link>
      </div>
    </div>
  );
}

/* ---------- Failsafe footer timestamp ---------- */

function UpcomingOnlyTimestamp({ generatedAt }: { generatedAt: string }) {
  const stamp = new Date(generatedAt).toLocaleString("en-US", {
    timeZone: "America/Chicago",
    hour: "numeric",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  });
  return (
    <span>
      Last updated: {stamp} CDT
    </span>
  );
}

/* ---------- Error state ---------- */

function DataUnavailable() {
  return (
    <section className="max-w-3xl mx-auto px-4 sm:px-6 py-20 text-center">
      <h1 className="text-3xl font-bold text-chalk-50">
        Daily card unavailable
      </h1>
      <p className="mt-3 text-chalk-300">
        Couldn&apos;t load <code>/data/daily/latest.json</code>. The morning
        build may not have completed yet, or the data file isn&apos;t deployed
        with this build.
      </p>
    </section>
  );
}

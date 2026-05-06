/**
 * Parlay viewer — /parlays
 *
 * Cross-sport view of every qualifying strict-policy ticket the
 * engines published today. Server-rendered with chip-link filters
 * (sport + universe) so a sharp can drill straight into "show me
 * NFL game-results parlays" without a page refresh blocking them.
 *
 * Honest empty state: when no qualified parlay exists for the
 * filtered slice, the page surfaces the engines' verbatim
 * "No qualified parlay today …" line via the `MetricTip` glossary
 * entry rather than a stand-in placeholder.
 */

import Link from "next/link";

import { ChalkboardBackground } from "../../components/ChalkboardBackground";
import { MetricTip } from "../../components/MetricTip";
import { ParlayCard } from "../../components/ParlayCard";
import { TransparencyNote } from "../../components/TransparencyNote";
import {
  DailyFeed,
  FeedParlay,
  SPORTS,
  SPORT_LABEL,
  SportKey,
  gameParlaysForSport,
  getDailyFeed,
  propParlaysForSport,
} from "../../lib/feed";


export const dynamic = "force-dynamic";


type UniverseKey = "all" | "game_results" | "player_props";


interface ParlaysRouteProps {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}


export default async function ParlaysPage({ searchParams }: ParlaysRouteProps) {
  const sp = (await searchParams) ?? {};
  const sportFilter = parseSport(sp.sport);
  const universeFilter = parseUniverse(sp.universe);

  const feed = await getDailyFeed();
  const tickets = collectTickets(feed, {
    sport: sportFilter,
    universe: universeFilter,
  });

  return (
    <>
      <section className="relative border-b border-chalkboard-600/40">
        <ChalkboardBackground />
        <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 py-12">
          <p className="font-chalk text-2xl text-elite/80 -rotate-1 inline-block">
            Parlay viewer
          </p>
          <h1 className="mt-1 text-3xl sm:text-4xl font-bold text-chalk-50">
            Every qualifying ticket — strict policy.
          </h1>
          <p className="mt-3 text-sm text-chalk-300 max-w-2xl">
            3–6 legs only. Each leg ≥4pp edge or ELITE tier. Combined
            EV positive after vig. When the math fails, the engine
            publishes nothing — Facts. Not Feelings.
          </p>
          {feed?.footer && (
            <p className="mt-2 text-[11px] text-chalk-500 font-mono">
              {feed.footer}
            </p>
          )}
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-5">
        <Filters
          sport={sportFilter}
          universe={universeFilter}
        />
        {tickets.length === 0 ? (
          <div className="chalk-card p-6 text-sm text-chalk-300">
            <p>
              <MetricTip term="no_qualified" label="No qualified parlay" />{" "}
              for this slice today. Adjust the filter or wait for
              tomorrow&apos;s card.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {tickets.map((t) => (
              <ParlayCard
                key={`${t.sport}-${t.universe}-${t.parlay.id}`}
                parlay={t.parlay}
                sport={t.sport}
                universe={t.universe}
              />
            ))}
          </div>
        )}
        <p className="text-[11px] text-chalk-500 leading-snug">
          Combined-ticket CLV is captured at first pitch / tip-off via
          the shared closing-line snapshot job. Per-ticket EV is
          computed at the engine&apos;s default 0.5u stake.
        </p>
      </section>

      <TransparencyNote />
    </>
  );
}


/* ---------- helpers ---------- */


function parseSport(v: string | string[] | undefined): SportKey | "all" {
  const raw = String(Array.isArray(v) ? v[0] : v ?? "").toLowerCase();
  return (SPORTS as readonly string[]).includes(raw)
    ? (raw as SportKey)
    : "all";
}


function parseUniverse(v: string | string[] | undefined): UniverseKey {
  const raw = String(Array.isArray(v) ? v[0] : v ?? "all").toLowerCase();
  if (raw === "game_results" || raw === "player_props") return raw;
  return "all";
}


interface CollectedTicket {
  sport: SportKey;
  universe: "game_results" | "player_props";
  parlay: FeedParlay;
}


function collectTickets(
  feed: DailyFeed | null,
  filter: { sport: SportKey | "all"; universe: UniverseKey },
): CollectedTicket[] {
  if (!feed) return [];
  const tickets: CollectedTicket[] = [];
  const sportsToConsider: readonly SportKey[] =
    filter.sport === "all" ? SPORTS : [filter.sport];
  for (const sport of sportsToConsider) {
    if (filter.universe === "all" || filter.universe === "game_results") {
      for (const p of gameParlaysForSport(feed, sport)) {
        tickets.push({ sport, universe: "game_results", parlay: p });
      }
    }
    if (filter.universe === "all" || filter.universe === "player_props") {
      for (const p of propParlaysForSport(feed, sport)) {
        tickets.push({ sport, universe: "player_props", parlay: p });
      }
    }
  }
  // Highest EV first so the most actionable ticket is the operator's
  // first read.
  tickets.sort(
    (a, b) => Number(b.parlay.ev_units) - Number(a.parlay.ev_units),
  );
  return tickets;
}


function Filters({
  sport, universe,
}: { sport: SportKey | "all"; universe: UniverseKey }) {
  return (
    <div className="chalk-card p-4 flex flex-wrap items-center gap-4">
      <FilterGroup label="Sport">
        <FilterChip
          href={hrefFor({ sport: "all", universe })}
          active={sport === "all"}
          label="All"
        />
        {SPORTS.map((s) => (
          <FilterChip
            key={s}
            href={hrefFor({ sport: s, universe })}
            active={sport === s}
            label={SPORT_LABEL[s]}
          />
        ))}
      </FilterGroup>
      <FilterGroup label="Universe">
        <FilterChip
          href={hrefFor({ sport, universe: "all" })}
          active={universe === "all"}
          label="All"
        />
        <FilterChip
          href={hrefFor({ sport, universe: "game_results" })}
          active={universe === "game_results"}
          label="Game results"
        />
        <FilterChip
          href={hrefFor({ sport, universe: "player_props" })}
          active={universe === "player_props"}
          label="Player props"
        />
      </FilterGroup>
    </div>
  );
}


function FilterGroup({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-chalk-500 mr-1">
        {label}
      </span>
      {children}
    </div>
  );
}


function FilterChip({
  href, active, label,
}: { href: string; active: boolean; label: string }) {
  const cls = active
    ? "bg-elite/20 text-elite border border-elite/50"
    : "bg-chalkboard-800/60 text-chalk-300 border border-chalkboard-700/60 hover:text-elite hover:border-elite/40";
  return (
    <Link
      href={href}
      className={`text-[11px] font-mono px-2 py-1 rounded transition-colors ${cls}`}
    >
      {label}
    </Link>
  );
}


function hrefFor(filter: {
  sport: SportKey | "all";
  universe: UniverseKey;
}): string {
  const params = new URLSearchParams();
  if (filter.sport !== "all") params.set("sport", filter.sport);
  if (filter.universe !== "all") params.set("universe", filter.universe);
  const qs = params.toString();
  return qs ? `/parlays?${qs}` : "/parlays";
}

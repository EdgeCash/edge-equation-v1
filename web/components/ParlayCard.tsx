/**
 * Renders one strict-policy parlay ticket (game-results or
 * player-props universe). Designed for the parlay viewer (`/parlays`)
 * and the per-sport hub (`/sport/[sport]`) — same payload shape from
 * `lib/feed.ts`.
 */

import type { FeedParlay, SportKey } from "../lib/feed";
import { SPORT_LABEL } from "../lib/feed";
import { MetricTip } from "./MetricTip";


interface ParlayCardProps {
  parlay: FeedParlay;
  sport: SportKey;
  universe: "game_results" | "player_props";
}


export function ParlayCard({ parlay, sport, universe }: ParlayCardProps) {
  const evNum = Number(parlay.ev_units);
  const evPositive = Number.isFinite(evNum) && evNum > 0;
  const edgeNum = Number(parlay.edge_pp);
  const jointPct = Math.round(Number(parlay.joint_prob_corr) * 1000) / 10;

  return (
    <article className="chalk-card p-5">
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-wider text-chalk-500">
            {SPORT_LABEL[sport]} · {universe.replace("_", "-")}
          </p>
          <p className="mt-1 text-chalk-50 font-semibold">
            {parlay.n_legs}-leg ticket @{" "}
            <span className="text-elite font-mono">
              {parlay.combined_decimal_odds.toFixed(2)}x
            </span>
            <span className="text-chalk-500 font-mono text-sm ml-2">
              ({prettyAmerican(parlay.combined_american_odds)})
            </span>
          </p>
        </div>
        <span
          className={
            "font-mono text-xs px-2 py-1 rounded border "
            + (evPositive
              ? "border-strong/40 text-strong bg-strong/10"
              : "border-chalk-500/30 text-chalk-300")
          }
        >
          EV {evPositive ? "+" : ""}
          {Number.isFinite(evNum) ? evNum.toFixed(3) : "—"}u
        </span>
      </header>

      <ol className="mt-4 space-y-2">
        {parlay.legs.map((leg, i) => (
          <li
            key={`${leg.market_type}-${i}`}
            className="border-l-2 border-elite/50 pl-3 text-sm"
          >
            <p className="text-chalk-50">{leg.selection}</p>
            <p className="text-[10px] uppercase tracking-wider text-chalk-500 mt-1">
              {leg.market_type.replace(/_/g, " ")} · tier {leg.tier} ·{" "}
              {prettyAmerican(leg.line_odds)} ·{" "}
              {(Number(leg.side_probability) * 100).toFixed(1)}% model prob
            </p>
          </li>
        ))}
      </ol>

      <dl className="mt-5 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <Stat
          label={<MetricTip term="joint_prob" label="Joint prob" />}
          value={`${jointPct.toFixed(1)}%`}
        />
        <Stat
          label="Independent prob"
          value={`${(Number(parlay.joint_prob_independent) * 100).toFixed(1)}%`}
        />
        <Stat
          label={<MetricTip term="implied_prob" label="Book implied" />}
          value={`${(Number(parlay.implied_prob) * 100).toFixed(1)}%`}
        />
        <Stat
          label={<MetricTip term="edge" label="Edge" />}
          value={`${edgeNum >= 0 ? "+" : ""}${edgeNum.toFixed(1)}pp`}
          highlight={edgeNum > 0}
        />
      </dl>

      <p className="mt-4 text-[10px] text-chalk-500 leading-snug border-t border-chalkboard-700/60 pt-3">
        Stake: {parlay.stake_units}u · Fair odds:{" "}
        {parlay.fair_decimal_odds > 0
          ? `${parlay.fair_decimal_odds.toFixed(2)}x`
          : "—"}{" "}
        · {parlay.note}
      </p>
    </article>
  );
}


function Stat({
  label, value, highlight = false,
}: {
  label: React.ReactNode;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-chalk-500">
        {label}
      </dt>
      <dd
        className={
          "mt-1 font-mono " + (highlight ? "text-elite" : "text-chalk-100")
        }
      >
        {value}
      </dd>
    </div>
  );
}


function prettyAmerican(odds: number | null | undefined): string {
  if (odds === null || odds === undefined || !Number.isFinite(odds)) return "—";
  const rounded = Math.round(odds);
  return rounded > 0 ? `+${rounded}` : `${rounded}`;
}

"use client";

import Link from "next/link";
import { useState } from "react";

import { TierBadge, tierFromEdge } from "./TierBadge";
import type { TodaysPlay } from "../lib/types";

const BET_TYPE_LABEL: Record<string, string> = {
  moneyline: "Moneyline",
  run_line: "Run Line",
  totals: "Game Total",
  first_5: "First 5 Innings",
  first_inning: "First Inning",
  team_totals: "Team Total",
};

interface Props {
  plays: TodaysPlay[];
}

export function DailyCardTable({ plays }: Props) {
  if (!plays || plays.length === 0) {
    return null;
  }

  return (
    <>
      {/* Mobile: stacked cards */}
      <div className="sm:hidden space-y-3">
        {plays.map((p, i) => (
          <PlayCardMobile play={p} key={i} />
        ))}
      </div>

      {/* Desktop: table */}
      <div className="hidden sm:block chalk-card overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Tier</th>
              <th>Matchup</th>
              <th>Pick</th>
              <th>Edge</th>
              <th>Model</th>
              <th>Market</th>
              <th>Book</th>
              <th>Units</th>
              <th aria-label="Deep dive"></th>
            </tr>
          </thead>
          <tbody className="text-chalk-100">
            {plays.map((p, i) => (
              <PlayRow play={p} key={i} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function PlayRow({ play }: { play: TodaysPlay }) {
  const tier = tierFromEdge(play.edge_pct, play.kelly_pct);
  const [open, setOpen] = useState(false);
  const teamLink = teamProfileHref(play);
  return (
    <>
      <tr>
        <td>
          <TierBadge tier={tier} size="sm" />
        </td>
        <td>
          <div className="text-chalk-50 font-medium">
            {teamLink ? (
              <Link
                href={teamLink.href}
                className="hover:text-elite transition-colors"
                title={`Open ${teamLink.label} profile`}
              >
                {play.matchup ?? "—"}
              </Link>
            ) : (
              <span>{play.matchup ?? "—"}</span>
            )}
          </div>
          {play.starting_pitchers && (
            <div className="text-[11px] text-chalk-500 mt-0.5 max-w-[16rem] truncate">
              {play.starting_pitchers}
            </div>
          )}
        </td>
        <td>
          <div className="text-elite font-mono text-sm">{play.pick ?? "—"}</div>
          <div className="text-[10px] uppercase tracking-wider text-chalk-500 mt-0.5">
            {BET_TYPE_LABEL[play.bet_type ?? ""] ?? play.bet_type}
          </div>
        </td>
        <td className="font-mono">
          <span className={play.edge_pct && play.edge_pct > 0 ? "text-elite" : "text-chalk-300"}>
            {play.edge_pct !== undefined && play.edge_pct !== null
              ? `${play.edge_pct >= 0 ? "+" : ""}${play.edge_pct.toFixed(2)}%`
              : "—"}
          </span>
        </td>
        <td className="font-mono text-chalk-300 text-xs">
          {play.model_prob !== undefined && play.model_prob !== null
            ? `${(play.model_prob * 100).toFixed(1)}%`
            : "—"}
        </td>
        <td className="font-mono text-chalk-100">
          {formatAmerican(play.market_odds_american)}
        </td>
        <td className="text-xs text-chalk-300">{play.book ?? "—"}</td>
        <td>
          <UnitChip play={play} />
        </td>
        <td>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="text-[10px] font-mono uppercase tracking-wider text-chalk-300 hover:text-elite transition-colors px-2 py-1 rounded border border-chalkboard-700/60"
            aria-expanded={open}
          >
            {open ? "Hide" : "Deep dive"}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={9} className="bg-chalkboard-900/60">
            <DeepDive play={play} />
          </td>
        </tr>
      )}
    </>
  );
}

function PlayCardMobile({ play }: { play: TodaysPlay }) {
  const tier = tierFromEdge(play.edge_pct, play.kelly_pct);
  const isElite = tier === "Signal Elite";
  const teamLink = teamProfileHref(play);
  const [open, setOpen] = useState(false);
  return (
    <article className={isElite ? "chalk-card-elite p-4" : "chalk-card p-4"}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-chalk-500">
            {BET_TYPE_LABEL[play.bet_type ?? ""] ?? play.bet_type}
          </p>
          <p className="mt-1 text-chalk-50 font-medium">
            {teamLink ? (
              <Link href={teamLink.href} className="hover:text-elite transition-colors">
                {play.matchup ?? "—"}
              </Link>
            ) : (
              play.matchup ?? "—"
            )}
          </p>
        </div>
        <TierBadge tier={tier} size="sm" />
      </header>

      <p className="mt-3 text-elite font-mono text-base">{play.pick ?? "—"}</p>
      {play.starting_pitchers && (
        <p className="mt-1 text-xs text-chalk-500">{play.starting_pitchers}</p>
      )}

      <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
        <Stat label="Edge" value={
          play.edge_pct !== undefined && play.edge_pct !== null
            ? `${play.edge_pct >= 0 ? "+" : ""}${play.edge_pct.toFixed(2)}%`
            : "—"
        } highlight={(play.edge_pct ?? 0) > 0} />
        <Stat label="Model" value={
          play.model_prob !== undefined && play.model_prob !== null
            ? `${(play.model_prob * 100).toFixed(1)}%`
            : "—"
        } />
        <Stat label="Market" value={formatAmerican(play.market_odds_american)} />
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-chalkboard-700/60 pt-3">
        <span className="text-[11px] text-chalk-500">{play.book ?? "—"}</span>
        <UnitChip play={play} />
      </div>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-3 text-[10px] font-mono uppercase tracking-wider text-chalk-300 hover:text-elite transition-colors"
        aria-expanded={open}
      >
        {open ? "Hide deep dive" : "Deep dive"}
      </button>
      {open && (
        <div className="mt-3 border-t border-chalkboard-700/60 pt-3">
          <DeepDive play={play} />
        </div>
      )}
    </article>
  );
}


/* ---------- Deep Dive (expanded body) ---------- */

function DeepDive({ play }: { play: TodaysPlay }) {
  const teamLink = teamProfileHref(play);
  const fairProb = play.model_prob;
  const marketProb =
    play.market_odds_dec && play.market_odds_dec > 1
      ? 1 / play.market_odds_dec
      : null;
  return (
    <div className="px-4 py-4 space-y-3 text-sm">
      <p className="text-xs uppercase tracking-wider text-chalk-300 font-mono">
        Why this pick
      </p>
      <ul className="grid sm:grid-cols-2 gap-3 text-xs">
        <DeepDiveBullet
          label="Model probability"
          value={
            fairProb !== undefined && fairProb !== null
              ? `${(fairProb * 100).toFixed(1)}%`
              : "—"
          }
          note="Engine's calibrated side probability."
        />
        <DeepDiveBullet
          label="Market (de-vig)"
          value={
            marketProb !== null
              ? `${(marketProb * 100).toFixed(1)}%`
              : "—"
          }
          note="Implied from current best price after vig removal."
        />
        <DeepDiveBullet
          label="Edge over close"
          value={
            play.edge_pct !== undefined && play.edge_pct !== null
              ? `${play.edge_pct >= 0 ? "+" : ""}${play.edge_pct.toFixed(2)}pp`
              : "—"
          }
          note="Model − market. CLV is logged at first pitch."
        />
        <DeepDiveBullet
          label="Stake (Kelly%)"
          value={
            play.kelly_pct !== undefined && play.kelly_pct !== null
              ? `${play.kelly_pct.toFixed(2)}%`
              : "—"
          }
          note={
            play.portfolio_scaled_from !== undefined
            && play.portfolio_scaled_from !== null
              ? `Capped from ${play.portfolio_scaled_from.toFixed(2)}% by the per-game portfolio cap.`
              : "Half-Kelly, mapped to the unit chip on the right."
          }
        />
      </ul>
      <div className="border-t border-chalkboard-700/60 pt-3 flex flex-wrap items-center gap-3">
        {teamLink && (
          <Link
            href={teamLink.href}
            className="text-[11px] font-mono text-elite hover:underline"
          >
            Open {teamLink.label} profile →
          </Link>
        )}
        <span className="text-[10px] text-chalk-500">
          All numbers are model outputs. Click any name for the full
          data view. Facts. Not Feelings.
        </span>
      </div>
    </div>
  );
}


function DeepDiveBullet({
  label, value, note,
}: { label: string; value: string; note: string }) {
  return (
    <li className="border border-chalkboard-700/60 rounded p-3">
      <p className="text-[10px] uppercase tracking-wider text-chalk-500">
        {label}
      </p>
      <p className="mt-1 font-mono text-chalk-100">{value}</p>
      <p className="mt-1 text-[10px] text-chalk-500 leading-snug">{note}</p>
    </li>
  );
}

function Stat({ label, value, highlight = false }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-chalk-500">{label}</p>
      <p className={`mt-0.5 font-mono ${highlight ? "text-elite" : "text-chalk-100"}`}>
        {value}
      </p>
    </div>
  );
}

function UnitChip({ play }: { play: TodaysPlay }) {
  const kelly = play.kelly_pct;
  if (kelly === undefined || kelly === null) {
    return <span className="text-chalk-500 text-xs">—</span>;
  }
  const units = unitsFromKelly(kelly);
  return (
    <div className="text-right">
      <p className="font-chalk text-xl text-elite leading-none">{units}u</p>
      {play.portfolio_scaled_from !== undefined && play.portfolio_scaled_from !== null && (
        <p className="text-[9px] text-chalk-500 mt-0.5">
          (capped from {unitsFromKelly(play.portfolio_scaled_from)}u)
        </p>
      )}
    </div>
  );
}

/* ---------- helpers ---------- */

function formatAmerican(am: number | null | undefined): string {
  if (am === null || am === undefined) return "—";
  return am > 0 ? `+${am}` : `${am}`;
}

/** Map half-Kelly% to the brand's discrete unit display.
 * Mirrors BRAND_GUIDE tier table. */
function unitsFromKelly(kellyPct: number): string {
  if (kellyPct >= 4) return "3";
  if (kellyPct >= 3) return "2";
  if (kellyPct >= 1.5) return "1.5";
  if (kellyPct >= 0.75) return "1";
  if (kellyPct >= 0.25) return "0.5";
  return "0";
}

/** Best-effort matchup → /team/mlb/<tricode> link. The legacy daily
 * card emits matchups like "NYY @ BOS"; we link the home tricode (the
 * second token) so the team profile page anchors on the host. */
function teamProfileHref(
  play: TodaysPlay,
): { href: string; label: string } | null {
  const matchup = String(play.matchup ?? "");
  if (!matchup) return null;
  const m = matchup.match(/^([A-Z0-9]{2,5})\s*@\s*([A-Z0-9]{2,5})/);
  if (!m) return null;
  const home = m[2].toLowerCase();
  return { href: `/team/mlb/${home}`, label: m[2] };
}

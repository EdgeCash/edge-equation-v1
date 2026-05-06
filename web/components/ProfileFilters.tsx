"use client";

import Link from "next/link";


export type ProfileResultFilter =
  | "all" | "WIN" | "LOSS" | "PUSH" | "PENDING";


interface ProfileFiltersProps {
  basePath: string;
  lastN: number | null;
  homeAway: "home" | "away" | null;
  result?: ProfileResultFilter;
  marketType?: string | null;
  /** Distinct market types observed in this profile's history —
   * the chip group renders one option per. Empty array hides
   * the group. */
  availableMarketTypes?: string[];
}


/**
 * Filter chips for the player / team profile pages.
 *
 * Plain anchor-based filtering — server component re-renders on
 * each click and reads the new query string. Lighter than wiring
 * client state for what's effectively a static toggle group.
 *
 * Filter groups (URL params):
 *   ?last=N       last N graded picks (5 / 10 / 20)
 *   ?ha=home|away home/away split
 *   ?result=WIN|LOSS|PUSH|PENDING
 *   ?mkt=<bet_type>   filter to a single market (e.g. PLAYER_PROP_HR)
 */
export function ProfileFilters({
  basePath,
  lastN,
  homeAway,
  result = "all",
  marketType = null,
  availableMarketTypes = [],
}: ProfileFiltersProps) {
  const lastOptions: Array<number | null> = [null, 5, 10, 20];
  const homeAwayOptions: Array<"home" | "away" | null> = [null, "home", "away"];
  const resultOptions: ProfileResultFilter[] =
    ["all", "WIN", "LOSS", "PUSH", "PENDING"];

  const baseFilters = { lastN, homeAway, result, marketType };

  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
      <FilterGroup label="Last N">
        {lastOptions.map((opt) => (
          <FilterChip
            key={`last-${opt ?? "all"}`}
            href={hrefFor(basePath, { ...baseFilters, lastN: opt })}
            active={opt === lastN}
            label={opt === null ? "All" : `Last ${opt}`}
          />
        ))}
      </FilterGroup>
      <FilterGroup label="Home / Away">
        {homeAwayOptions.map((opt) => (
          <FilterChip
            key={`ha-${opt ?? "all"}`}
            href={hrefFor(basePath, { ...baseFilters, homeAway: opt })}
            active={opt === homeAway}
            label={opt === null ? "Both" : opt === "home" ? "Home" : "Away"}
          />
        ))}
      </FilterGroup>
      <FilterGroup label="Result">
        {resultOptions.map((opt) => (
          <FilterChip
            key={`result-${opt}`}
            href={hrefFor(basePath, { ...baseFilters, result: opt })}
            active={opt === result}
            label={opt === "all" ? "All" : opt}
          />
        ))}
      </FilterGroup>
      {availableMarketTypes.length > 0 && (
        <FilterGroup label="Market">
          <FilterChip
            href={hrefFor(basePath, { ...baseFilters, marketType: null })}
            active={!marketType}
            label="All"
          />
          {availableMarketTypes.map((m) => (
            <FilterChip
              key={`mkt-${m}`}
              href={hrefFor(basePath, { ...baseFilters, marketType: m })}
              active={m === marketType}
              label={m}
            />
          ))}
        </FilterGroup>
      )}
    </div>
  );
}


function FilterGroup({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
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


function hrefFor(
  base: string,
  filters: {
    lastN: number | null;
    homeAway: "home" | "away" | null;
    result: ProfileResultFilter;
    marketType: string | null;
  },
): string {
  const params = new URLSearchParams();
  if (filters.lastN) params.set("last", String(filters.lastN));
  if (filters.homeAway) params.set("ha", filters.homeAway);
  if (filters.result && filters.result !== "all") {
    params.set("result", filters.result);
  }
  if (filters.marketType) params.set("mkt", filters.marketType);
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

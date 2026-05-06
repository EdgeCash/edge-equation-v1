"use client";

import Link from "next/link";


interface ProfileFiltersProps {
  basePath: string;
  lastN: number | null;
  homeAway: "home" | "away" | null;
}


/**
 * Filter chips for the player / team profile pages.
 *
 * Plain anchor-based filtering — server component re-renders on
 * each click and reads the new query string. Lighter than wiring
 * client state for what's effectively a static toggle group.
 */
export function ProfileFilters({
  basePath, lastN, homeAway,
}: ProfileFiltersProps) {
  const lastOptions: Array<number | null> = [null, 5, 10, 20];
  const homeAwayOptions: Array<"home" | "away" | null> = [null, "home", "away"];

  return (
    <div className="flex flex-wrap items-center gap-3">
      <FilterGroup label="Last N">
        {lastOptions.map((opt) => (
          <FilterChip
            key={`last-${opt ?? "all"}`}
            href={hrefFor(basePath, { lastN: opt, homeAway })}
            active={opt === lastN}
            label={opt === null ? "All" : `Last ${opt}`}
          />
        ))}
      </FilterGroup>
      <FilterGroup label="Home / Away">
        {homeAwayOptions.map((opt) => (
          <FilterChip
            key={`ha-${opt ?? "all"}`}
            href={hrefFor(basePath, { lastN, homeAway: opt })}
            active={opt === homeAway}
            label={opt === null ? "Both" : opt === "home" ? "Home" : "Away"}
          />
        ))}
      </FilterGroup>
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
  filters: { lastN: number | null; homeAway: "home" | "away" | null },
): string {
  const params = new URLSearchParams();
  if (filters.lastN) params.set("last", String(filters.lastN));
  if (filters.homeAway) params.set("ha", filters.homeAway);
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

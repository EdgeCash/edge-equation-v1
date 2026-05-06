"use client";

import Link from "next/link";
import { useState } from "react";

import { SearchBar } from "./SearchBar";

const NAV_LINKS = [
  { href: "/daily-card", label: "Daily Card" },
  { href: "/parlays", label: "Parlays" },
  { href: "/track-record", label: "Track Record" },
  { href: "/ledger", label: "Ledger" },
  { href: "/methodology", label: "Methodology" },
];

const SPORT_LINKS = [
  { href: "/sport/mlb", label: "MLB" },
  { href: "/sport/wnba", label: "WNBA" },
  { href: "/sport/nfl", label: "NFL" },
  { href: "/sport/ncaaf", label: "NCAAF" },
];

export function Navigation() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-30 border-b border-chalkboard-600/40 bg-chalkboard-950/85 backdrop-blur-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
        <Link
          href="/"
          className="flex items-center gap-2 text-chalk-50 font-semibold text-lg shrink-0"
        >
          <Logo />
          <span>
            Edge<span className="text-elite">Equation</span>
          </span>
        </Link>

        {/* Search — desktop only, takes the available middle space */}
        <div className="hidden md:flex flex-1 justify-center">
          <SearchBar />
        </div>

        {/* Desktop nav */}
        <nav className="hidden md:flex items-center gap-6 text-sm text-chalk-300 shrink-0">
          <SportsMenu />
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="hover:text-elite transition-colors"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        {/* Mobile toggle */}
        <button
          onClick={() => setOpen((o) => !o)}
          className="md:hidden text-chalk-100 p-2 rounded hover:bg-chalkboard-800"
          aria-label="Toggle navigation"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="22"
            height="22"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {open ? (
              <>
                <path d="M18 6 6 18" />
                <path d="m6 6 12 12" />
              </>
            ) : (
              <>
                <path d="M3 12h18" />
                <path d="M3 6h18" />
                <path d="M3 18h18" />
              </>
            )}
          </svg>
        </button>
      </div>

      {/* Mobile menu */}
      {open && (
        <nav className="md:hidden border-t border-chalkboard-600/40 bg-chalkboard-950">
          <div className="max-w-7xl mx-auto px-4 py-3 flex flex-col gap-2">
            <SearchBar />
            <div className="flex flex-col gap-1 pt-2">
              {NAV_LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className="px-3 py-2 rounded text-chalk-100 hover:bg-chalkboard-800 hover:text-elite transition-colors"
                >
                  {link.label}
                </Link>
              ))}
              <div className="px-3 pt-2 mt-1 border-t border-chalkboard-700/60">
                <p className="text-[10px] uppercase tracking-wider text-chalk-500 mb-1">
                  Sports
                </p>
                <div className="grid grid-cols-2 gap-1">
                  {SPORT_LINKS.map((s) => (
                    <Link
                      key={s.href}
                      href={s.href}
                      onClick={() => setOpen(false)}
                      className="px-3 py-2 rounded text-chalk-100 hover:bg-chalkboard-800 hover:text-elite transition-colors text-sm"
                    >
                      {s.label}
                    </Link>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </nav>
      )}
    </header>
  );
}


/** Desktop "Sports" dropdown — hover-opens via group-hover so we
 * don't pay for client state on the sticky nav. */
function SportsMenu() {
  return (
    <div className="relative group">
      <button
        type="button"
        className="hover:text-elite transition-colors flex items-center gap-1"
        aria-haspopup="true"
        aria-expanded="false"
      >
        Sports
        <svg
          width="11" height="11" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
          strokeLinejoin="round" aria-hidden
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      <div className="pointer-events-none invisible absolute right-0 top-full pt-3 z-40 group-hover:visible group-hover:pointer-events-auto group-focus-within:visible group-focus-within:pointer-events-auto">
        <div className="rounded-md border border-chalkboard-600/70 bg-chalkboard-950/95 backdrop-blur-md shadow-[0_8px_32px_rgba(0,0,0,0.4)] min-w-[160px]">
          <ul className="py-1">
            {SPORT_LINKS.map((s) => (
              <li key={s.href}>
                <Link
                  href={s.href}
                  className="block px-3 py-2 text-sm text-chalk-100 hover:bg-chalkboard-800 hover:text-elite transition-colors"
                >
                  {s.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/** Compact wordmark icon — stylized "Σ" for summation, our brand symbol. */
function Logo() {
  return (
    <svg
      width="26"
      height="26"
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden
    >
      <rect width="32" height="32" rx="6" fill="#0f1623" />
      <text
        x="50%"
        y="55%"
        textAnchor="middle"
        dominantBaseline="middle"
        fontFamily="ui-monospace, monospace"
        fontSize="22"
        fontWeight="700"
        fill="#38bdf8"
      >
        Σ
      </text>
    </svg>
  );
}

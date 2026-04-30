// Site-wide banner announcing that we're in public testing.
// Lives at the top of every page via Layout. Copy is the verbatim phrasing
// from the V4 brief.

import Link from "next/link";

export default function TestingBanner() {
  return (
    <div className="border-b border-conviction-elite/30 bg-conviction-eliteSoft/40">
      <div className="mx-auto max-w-6xl px-6 py-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] sm:text-xs leading-snug">
        <span className="font-mono uppercase tracking-[0.22em] text-conviction-elite">
          Public Testing · v1
        </span>
        <span className="text-edge-textDim">
          Results are tracked transparently. These are data projections, not
          betting advice. Bet responsibly.
        </span>
        <Link
          href="/about"
          className="ml-auto font-mono uppercase tracking-[0.22em] text-conviction-elite/90 hover:text-conviction-elite border-b border-transparent hover:border-conviction-elite pb-0.5"
        >
          Our Story →
        </Link>
      </div>
    </div>
  );
}

// Site-wide banner announcing free public testing. We're publishing
// every pick openly, with the public track record as the proof. The
// banner links to the track record so visitors land directly on the
// receipts.

import Link from "next/link";

export default function TestingBanner() {
  return (
    <div className="border-b border-conviction-elite/30 bg-conviction-eliteSoft/40">
      <div className="mx-auto max-w-6xl px-6 py-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] sm:text-xs leading-snug">
        <span className="font-mono uppercase tracking-[0.22em] text-conviction-elite">
          Free · Public Testing
        </span>
        <span className="text-edge-textDim">
          Every pick logged honestly. Data, not betting advice. Bet
          responsibly.
        </span>
        <Link
          href="/track-record"
          className="ml-auto font-mono uppercase tracking-[0.22em] text-conviction-elite/90 hover:text-conviction-elite border-b border-transparent hover:border-conviction-elite pb-0.5"
        >
          See Track Record →
        </Link>
      </div>
    </div>
  );
}

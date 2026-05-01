import Link from "next/link";

import LogoMark from "./LogoMark";

const SECONDARY = [
  { href: "/track-record", label: "Track Record" },
  { href: "/archive", label: "Archive" },
  { href: "/grade-history", label: "Grades" },
  { href: "/contact", label: "Contact" },
];

export default function Footer() {
  return (
    <footer className="border-t border-edge-line mt-24">
      <div className="mx-auto max-w-6xl px-6 py-12 grid gap-10 md:grid-cols-[2fr_1fr_1fr]">
        <div>
          <div className="flex items-center gap-3">
            <LogoMark className="h-12 w-12 shrink-0" />
            <div>
              <div className="font-display text-2xl tracking-tightest text-edge-text leading-none">
                Edge Equation
              </div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.22em] text-edge-accent">
                Facts. Not Feelings.
              </div>
            </div>
          </div>
          <p className="mt-4 max-w-prose text-sm text-edge-textDim leading-relaxed">
            We help people become better bettors by publishing the data and the
            reasoning behind every call. The picks are free. The thinking is
            the product.
          </p>
        </div>

        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint mb-3">
            More
          </div>
          <ul className="space-y-2">
            {SECONDARY.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="font-mono text-[11px] uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-accent transition-colors"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint mb-3">
            Follow
          </div>
          <ul className="space-y-2">
            <li>
              <Link
                href="https://x.com/edgeequation"
                className="font-mono text-[11px] uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-accent transition-colors"
              >
                X / Twitter
              </Link>
            </li>
            <li>
              <Link
                href="mailto:contact@edgeequation.com"
                className="font-mono text-[11px] uppercase tracking-[0.2em] text-edge-textDim hover:text-edge-accent transition-colors"
              >
                Email
              </Link>
            </li>
          </ul>
        </div>
      </div>

      <div className="border-t border-edge-line">
        <div className="mx-auto max-w-6xl px-6 py-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
            © {new Date().getFullYear()} Edge Equation · For entertainment and
            educational use. 21+. Please bet responsibly.
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint">
            No guarantees. No locks. Just the math.
          </p>
        </div>
        <div className="mx-auto max-w-6xl px-6 pb-6">
          <p className="text-[11px] leading-relaxed text-edge-textFaint">
            <strong className="text-edge-textDim">Disclaimer.</strong>{" "}
            Edge Equation publishes data, projections, and historical
            outcomes. Nothing on this site is financial or wagering advice.
            Past performance does not predict future results. If you or
            someone you know has a gambling problem, call{" "}
            <a
              href="tel:18004262537"
              className="text-edge-textDim underline underline-offset-2"
            >
              1-800-GAMBLER
            </a>
            .
          </p>
        </div>
      </div>
    </footer>
  );
}

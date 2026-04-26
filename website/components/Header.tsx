import Link from "next/link";

const nav = [
  { href: "/", label: "Home" },
  { href: "/daily-edge", label: "Daily Edge" },
  { href: "/archive", label: "Archive" },
  { href: "/grade-history", label: "Grades" },
  { href: "/premium-edge", label: "Premium" },
  { href: "/about", label: "About" },
  { href: "/account", label: "Account" },
];

// Sigma glyph drawn as inline SVG so it picks up currentColor + scales
// crisply at any DPR. The chalk-style stroke + faint underline read as
// a hand-drawn mark next to the wordmark.
function SigmaMark({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 4 H19 L11 12 L19 20 H5" />
      <path d="M3 22 H21" opacity="0.35" />
    </svg>
  );
}

export default function Header() {
  return (
    <header className="border-b border-edge-line relative">
      {/* faint cyan hairline pulse just under the border */}
      <div className="absolute inset-x-0 -bottom-px h-px bg-gradient-to-r from-transparent via-edge-accent/40 to-transparent pointer-events-none" />
      <div className="mx-auto max-w-6xl px-6 py-6 flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <Link href="/" className="group inline-flex items-start gap-3">
          <SigmaMark className="w-7 h-7 text-edge-accent mt-1 transition-transform group-hover:rotate-[-3deg] group-hover:scale-105" />
          <div>
            <div className="flex items-baseline gap-3">
              <span className="font-display text-2xl tracking-tightest text-edge-text">
                Edge Equation
              </span>
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-accent">
                v1
              </span>
            </div>
            <div className="font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textDim mt-1">
              Facts. Not Feelings.
            </div>
          </div>
        </Link>
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="font-mono text-[11px] uppercase tracking-[0.18em] text-edge-textDim hover:text-edge-accent transition-colors"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}

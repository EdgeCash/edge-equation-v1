import Link from "next/link";

const nav = [
  { href: "/", label: "Home" },
  { href: "/daily-edge", label: "Daily Edge" },
  { href: "/premium-edge", label: "Premium" },
  { href: "/about", label: "About" },
  { href: "/contact", label: "Contact" },
];

export default function Header() {
  return (
    <header className="border-b border-edge-line">
      <div className="mx-auto max-w-6xl px-6 py-6 flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <Link href="/" className="group">
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

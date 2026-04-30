import Link from "next/link";
import { useRouter } from "next/router";
import { useState } from "react";

const PRIMARY_NAV = [
  { href: "/", label: "Home" },
  { href: "/daily-edge", label: "Daily Edge" },
  { href: "/about", label: "About" },
  { href: "/engine", label: "The Engine" },
  { href: "/premium-edge", label: "Premium" },
  { href: "/learn", label: "Learn" },
];

export default function Header() {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  const isActive = (href: string) =>
    href === "/" ? router.pathname === "/" : router.pathname.startsWith(href);

  return (
    <header className="border-b border-edge-line bg-ink-950/80 backdrop-blur sticky top-0 z-30">
      <div className="mx-auto max-w-6xl px-6 py-5 flex items-center justify-between gap-4">
        <Link href="/" className="group" onClick={() => setOpen(false)}>
          <div className="flex items-baseline gap-3">
            <span className="font-display text-2xl tracking-tightest text-edge-text">
              Edge Equation
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-accent">
              v4
            </span>
          </div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textDim mt-1">
            Facts. Not Feelings.
          </div>
        </Link>

        <nav className="hidden md:flex items-center gap-x-6">
          {PRIMARY_NAV.map((item) => {
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={
                  "font-mono text-[11px] uppercase tracking-[0.2em] transition-colors " +
                  (active
                    ? "text-edge-accent"
                    : "text-edge-textDim hover:text-edge-text")
                }
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <button
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="md:hidden inline-flex items-center justify-center w-10 h-10 border border-edge-line rounded-sm text-edge-textDim hover:text-edge-accent hover:border-edge-accent transition-colors"
        >
          <span className="sr-only">Menu</span>
          <span aria-hidden="true" className="font-mono text-sm">
            {open ? "×" : "≡"}
          </span>
        </button>
      </div>

      {open && (
        <nav className="md:hidden border-t border-edge-line bg-ink-950">
          <div className="mx-auto max-w-6xl px-6 py-4 flex flex-col gap-3">
            {PRIMARY_NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  className={
                    "font-mono text-[12px] uppercase tracking-[0.2em] py-2 " +
                    (active ? "text-edge-accent" : "text-edge-textDim")
                  }
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </nav>
      )}
    </header>
  );
}

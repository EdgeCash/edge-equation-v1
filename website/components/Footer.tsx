import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-edge-line mt-24 relative">
      {/* faint cyan hairline pulse, mirroring the header */}
      <div className="absolute inset-x-0 -top-px h-px bg-gradient-to-r from-transparent via-edge-accent/40 to-transparent pointer-events-none" />
      <div className="mx-auto max-w-6xl px-6 py-10 flex flex-col sm:flex-row gap-4 sm:items-center sm:justify-between">
        <div className="flex items-baseline gap-3 font-mono text-[11px] uppercase tracking-[0.2em] text-edge-textDim">
          <span className="text-edge-accent">∎</span>
          <span>© {new Date().getFullYear()} Edge Equation · Q.E.D.</span>
        </div>
        <div className="flex gap-6 font-mono text-[11px] uppercase tracking-[0.2em]">
          <Link
            href="https://x.com/edgeequation"
            className="text-edge-textDim hover:text-edge-accent transition-colors"
          >
            X / Twitter
          </Link>
          <Link
            href="mailto:contact@edgeequation.com"
            className="text-edge-textDim hover:text-edge-accent transition-colors"
          >
            Contact
          </Link>
        </div>
      </div>
    </footer>
  );
}

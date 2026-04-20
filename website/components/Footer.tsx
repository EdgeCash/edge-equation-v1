import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-edge-line mt-24">
      <div className="mx-auto max-w-6xl px-6 py-10 flex flex-col sm:flex-row gap-4 sm:items-center sm:justify-between">
        <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-edge-textDim">
          © {new Date().getFullYear()} Edge Equation
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

import Link from "next/link";
import Layout from "@/components/Layout";

export default function Contact() {
  return (
    <Layout title="Contact">
      <div className="annotation mb-4 flex items-center gap-3">
        <span className="text-edge-accent">→</span>
        <span>Get in touch</span>
      </div>
      <h1 className="font-display font-light text-5xl sm:text-6xl tracking-tightest leading-none">
        Cont<span className="italic text-edge-accent chalk-underline accent-glow">act</span>
      </h1>

      <p className="mt-6 max-w-prose text-edge-textDim leading-relaxed">
        For partnerships, press, or data inquiries, reach out via email. For
        everything else — daily cards, model takes, and launch updates — X is
        the fastest way to stay in the loop.
      </p>

      <div className="mt-14 grid grid-cols-1 md:grid-cols-2 gap-px bg-edge-line">
        <div className="bg-ink-900 p-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-edge-accent">
            Email
          </div>
          <Link
            href="mailto:contact@edgeequation.com"
            className="mt-3 block font-display text-3xl tracking-tightest text-edge-text hover:text-edge-accent transition-colors"
          >
            contact@
            <wbr />
            edgeequation.com
          </Link>
          <p className="mt-3 text-edge-textDim text-sm">
            Partnerships, press, data.
          </p>
        </div>

        <div className="bg-ink-900 p-8">
          <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-edge-accent">
            Social
          </div>
          <Link
            href="https://x.com/edgeequation"
            className="mt-3 block font-display text-3xl tracking-tightest text-edge-text hover:text-edge-accent transition-colors"
          >
            @edgeequation
          </Link>
          <p className="mt-3 text-edge-textDim text-sm">
            Daily cards, model takes, launch updates.
          </p>
        </div>
      </div>
    </Layout>
  );
}

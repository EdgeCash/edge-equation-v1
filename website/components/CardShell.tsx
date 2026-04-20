import { ReactNode } from "react";

type CardShellProps = {
  headline?: string;
  subhead?: string;
  eyebrow?: string;
  children: ReactNode;
};

export default function CardShell({
  headline,
  subhead,
  eyebrow,
  children,
}: CardShellProps) {
  return (
    <article className="relative bg-ink-900/80 backdrop-blur border border-edge-line rounded-sm p-8 sm:p-10">
      {/* Corner tick marks for editorial feel */}
      <div className="absolute top-0 left-0 w-3 h-3 border-l border-t border-edge-accent/60" />
      <div className="absolute top-0 right-0 w-3 h-3 border-r border-t border-edge-accent/60" />
      <div className="absolute bottom-0 left-0 w-3 h-3 border-l border-b border-edge-accent/60" />
      <div className="absolute bottom-0 right-0 w-3 h-3 border-r border-b border-edge-accent/60" />

      {eyebrow && (
        <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-edge-accent mb-4">
          {eyebrow}
        </div>
      )}
      {headline && (
        <h2 className="font-display text-3xl sm:text-4xl tracking-tightest leading-[1.05] text-edge-text">
          {headline}
        </h2>
      )}
      {subhead && (
        <p className="mt-3 text-edge-textDim max-w-prose">{subhead}</p>
      )}
      <div className="mt-8">{children}</div>
    </article>
  );
}

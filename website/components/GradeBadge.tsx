// Grade letter with a color keyed to confidence tier.
// Keeps the "no emoji unless requested" rule; uses Tailwind accent classes.

type GradeBadgeProps = {
  grade: string;
  className?: string;
};

const GRADE_COLORS: Record<string, string> = {
  "A+": "bg-edge-accent text-ink-950",
  "A": "bg-edge-accent/80 text-ink-950",
  "B": "bg-edge-text/80 text-ink-950",
  "C": "bg-ink-700 text-edge-textDim",
  "D": "bg-ink-800 text-edge-textDim",
  "F": "bg-ink-900 text-edge-textDim border border-edge-line",
};

export default function GradeBadge({ grade, className = "" }: GradeBadgeProps) {
  const colorClass = GRADE_COLORS[grade] ?? GRADE_COLORS["C"];
  return (
    <span
      className={
        "inline-flex items-center justify-center min-w-[2.5rem] px-2 py-0.5 " +
        "font-mono text-xs uppercase tracking-[0.15em] " +
        colorClass + " " + className
      }
    >
      {grade}
    </span>
  );
}

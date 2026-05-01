// Brand mark — inline SVG sigma + upward chart line. Inspired by the
// AI brand graphics (chalkboard sigma + candlesticks + cyan trend
// arrow). Built as inline SVG so it:
//   * scales perfectly at every breakpoint
//   * has zero HTTP cost (no PNG fetch)
//   * stays editable without regenerating images
//
// Two design layers:
//   1. Light-gray Σ outline — the editorial wordmark equivalent.
//   2. Cyan trend line that zigzags upward inside the sigma's negative
//      space, ending in a small arrow head. The "edge" in the equation.
// Plus a soft cyan glow circle behind it that nods to the AI graphic
// vibe without going full painted-canvas.
//
// Mark is square (1:1). Sized via className. The Header uses h-9 w-9;
// the Footer uses h-12 w-12. Anywhere else, scale freely.

type Props = {
  className?: string;
  ariaLabel?: string;
};


export default function LogoMark({
  className = "",
  ariaLabel = "Edge Equation logo",
}: Props) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      role="img"
      aria-label={ariaLabel}
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Soft cyan halo behind the mark — gives the "glowing accent"
          feel from the brand graphics without dominating the icon. */}
      <circle cx="32" cy="32" r="22" fill="rgba(34, 211, 255, 0.08)" />

      {/* Σ outline. Three strokes: top horizontal, V to center, bottom
          horizontal. Light gray to read on dark surfaces; rounded
          line caps + joins so small sizes still render crisply. */}
      <path
        d="M 19 16 H 45 L 31 32 L 45 48 H 19"
        stroke="rgb(229, 236, 242)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {/* Cyan trend line — zigzags upward inside the sigma's whitespace.
          Reads as "the chart inside the equation." The line ends just
          before the right edge so the arrow head can attach cleanly. */}
      <path
        d="M 22 44 L 28 38 L 34 40 L 40 30"
        stroke="rgb(34, 211, 255)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      {/* Arrow head — a small L pointing up-right. */}
      <path
        d="M 36 30 L 40 30 L 40 34"
        stroke="rgb(34, 211, 255)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}

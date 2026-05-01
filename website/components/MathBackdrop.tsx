// Decorative SVG backdrop — sigma symbol, distribution curve, and a
// pinned equation, all rendered as low-opacity inline SVG. Designed to
// be dropped inside a `relative` container; the component absolutely
// positions itself behind the content via `pointer-events-none`.
//
// Goal: match the chalkboard / math-lab aesthetic of the AI brand
// graphics without shipping image assets. Total weight is <2 KB
// inlined; mobile-fast.
//
// Variants:
//   "hero"     — denser composition, sigma in the lower-left, curve
//                stretched across the right half, equation pinned
//                top-right. Use behind the homepage hero.
//   "section"  — restrained: only the sigma + a single hairline curve.
//                Use behind page-header blocks like /track-record.

type Variant = "hero" | "section";

type Props = {
  variant?: Variant;
  // Tweak the master opacity if a particular section needs more or
  // less texture. Default 1.0 (component already targets ~6-10%
  // opacity per stroke / fill internally).
  intensity?: number;
};


export default function MathBackdrop({
  variant = "hero",
  intensity = 1.0,
}: Props) {
  const op = (base: number) =>
    Math.max(0, Math.min(1, base * intensity));

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 overflow-hidden select-none"
    >
      {/* Sigma — the brand mark, drawn as a stroke. Low opacity so it
          reads as texture, not foreground. */}
      <svg
        className={
          variant === "hero"
            ? "absolute -left-8 -bottom-12 sm:left-4 sm:bottom-4 h-64 w-64 sm:h-80 sm:w-80"
            : "absolute -left-6 -top-6 h-40 w-40"
        }
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M 25 18 H 75 L 50 50 L 75 82 H 25"
          stroke="rgb(34, 211, 255)"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ opacity: op(0.08) }}
        />
        <path
          d="M 25 18 H 75 L 50 50 L 75 82 H 25"
          stroke="rgb(34, 211, 255)"
          strokeWidth="0.4"
          style={{ opacity: op(0.18) }}
        />
      </svg>

      {/* Normal distribution curve — only on the hero variant.
          Stretched across the right half of the frame. */}
      {variant === "hero" && (
        <svg
          className="absolute right-0 top-1/3 h-48 w-3/5 sm:h-64"
          viewBox="0 0 600 200"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="none"
        >
          {/* Gaussian-ish path. Hand-tuned bezier so it looks like a
              stats textbook curve without needing a real distribution
              calc. */}
          <path
            d="M 0 180
               C 120 180, 220 178, 280 90
               C 320 30, 350 30, 380 90
               C 440 178, 540 180, 600 180"
            stroke="rgb(34, 211, 255)"
            strokeWidth="1"
            style={{ opacity: op(0.18) }}
          />
          {/* Faint baseline grid above the curve. */}
          <line x1="0" y1="180" x2="600" y2="180"
                stroke="rgb(255, 255, 255)" strokeWidth="0.5"
                style={{ opacity: op(0.10) }} />
          {/* μ tick. */}
          <line x1="330" y1="180" x2="330" y2="170"
                stroke="rgb(34, 211, 255)" strokeWidth="0.8"
                style={{ opacity: op(0.30) }} />
          <text
            x="332" y="195"
            fontFamily="JetBrains Mono, monospace"
            fontSize="11"
            fill="rgb(34, 211, 255)"
            style={{ opacity: op(0.4) }}
          >
            μ
          </text>
        </svg>
      )}

      {/* Equation — pinned in a corner. Text-only SVG so it scales
          cleanly. Hidden on small screens to keep the hero clean. */}
      {variant === "hero" && (
        <svg
          className="absolute right-3 top-3 hidden sm:block h-8 w-64"
          viewBox="0 0 260 32"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <text
            x="0" y="22"
            fontFamily="JetBrains Mono, monospace"
            fontSize="13"
            fill="rgb(229, 236, 242)"
            style={{ opacity: op(0.20) }}
          >
            σ = √( Σ(x − μ)² / N )
          </text>
        </svg>
      )}

      {/* A faint candlestick chart, hero only — bottom-right. Six
          static bars; hand-positioned to look like a small market
          tick view. */}
      {variant === "hero" && (
        <svg
          className="absolute right-0 bottom-2 hidden md:block h-32 w-72"
          viewBox="0 0 280 128"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          {[
            { x: 30,  ot: 88, ct: 64, lo: 96, hi: 56, up: true  },
            { x: 70,  ot: 64, ct: 80, lo: 92, hi: 56, up: false },
            { x: 110, ot: 80, ct: 60, lo: 96, hi: 52, up: true  },
            { x: 150, ot: 60, ct: 48, lo: 72, hi: 36, up: true  },
            { x: 190, ot: 48, ct: 56, lo: 64, hi: 36, up: false },
            { x: 230, ot: 56, ct: 32, lo: 64, hi: 24, up: true  },
          ].map((c, i) => {
            const color = c.up ? "rgb(34, 211, 255)" : "rgb(229, 236, 242)";
            const top = Math.min(c.ot, c.ct);
            const h = Math.abs(c.ot - c.ct) || 2;
            return (
              <g key={i} style={{ opacity: op(0.22) }}>
                <line x1={c.x} y1={c.hi} x2={c.x} y2={c.lo}
                      stroke={color} strokeWidth="0.8" />
                <rect x={c.x - 5} y={top} width="10" height={h}
                      fill={color} />
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

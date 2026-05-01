// Single source of truth for the V4 Conviction system (post 2026-05-01
// unification).
//
// Every pick on the site — regardless of sport, market, or side — maps
// to exactly one tier here. The tier drives the color, the label, and
// the visual weight. Cold-traffic-friendly semantics:
//
//   Electric Blue → Strong upside (Elite)
//   Deep Green    → Strong upside
//   Amber         → Moderate
//   Slate         → Lean (informational)
//   Red           → No play / pass — the universal "stop" semantic
//
// Earlier the system tried Red as a positive-framing "Strong YRFI"
// color (PR #96). Cold visitors read Red as "don't trust this," so
// the YRFI-side override was dropped 2026-05-01. Red is exclusively
// NO_PLAY now; STRONG works on either side of a market.

export type ConvictionTier =
  | "ELITE"
  | "STRONG"
  | "MODERATE"
  | "LEAN"
  | "NO_PLAY";

export interface ConvictionMeta {
  tier: ConvictionTier;
  label: string;          // short label for badges
  longLabel: string;      // longer description for the key
  description: string;    // one-sentence explanation
  // Tailwind class fragments — kept as strings so JIT can see them.
  textClass: string;
  bgSoftClass: string;
  borderClass: string;
  dotClass: string;
}

export const CONVICTION: Record<ConvictionTier, ConvictionMeta> = {
  ELITE: {
    tier: "ELITE",
    label: "Electric Blue",
    longLabel: "Electric Blue · Elite",
    description:
      "Highest-conviction plays. Largest modeled edge with stable inputs. Rare by design.",
    textClass: "text-conviction-elite",
    bgSoftClass: "bg-conviction-eliteSoft",
    borderClass: "border-conviction-elite",
    dotClass: "bg-conviction-elite",
  },
  STRONG: {
    tier: "STRONG",
    label: "Deep Green",
    longLabel: "Deep Green · Strong",
    description:
      "Strong conviction read on either side of the market. Solid edge with grade A inputs — the kind of play we run every day.",
    textClass: "text-conviction-strong",
    bgSoftClass: "bg-conviction-strongSoft",
    borderClass: "border-conviction-strong",
    dotClass: "bg-conviction-strong",
  },
  MODERATE: {
    tier: "MODERATE",
    label: "Amber",
    longLabel: "Amber · Moderate",
    description:
      "Modest edge or a noisier signal. We publish it for transparency, not to chase.",
    textClass: "text-conviction-moderate",
    bgSoftClass: "bg-conviction-moderateSoft",
    borderClass: "border-conviction-moderate",
    dotClass: "bg-conviction-moderate",
  },
  LEAN: {
    tier: "LEAN",
    label: "Slate",
    longLabel: "Slate · Lean",
    description:
      "Small directional edge. Logged for the record; not a recommended play.",
    textClass: "text-conviction-neutral",
    bgSoftClass: "bg-conviction-neutralSoft",
    borderClass: "border-conviction-neutral",
    dotClass: "bg-conviction-neutral",
  },
  NO_PLAY: {
    tier: "NO_PLAY",
    label: "Red",
    longLabel: "Red · No Play",
    description:
      "Model and market agree, or the modeled edge is too small to matter. We pass — and saying that openly is the whole point.",
    textClass: "text-conviction-fade",
    bgSoftClass: "bg-conviction-fadeSoft",
    borderClass: "border-conviction-fade",
    dotClass: "bg-conviction-fade",
  },
};

// Ordered list for the visual key — Elite at the top, NO_PLAY (Red)
// at the bottom of the conviction ladder. Reading top-to-bottom you
// move from "highest conviction" to "no play."
export const CONVICTION_ORDER: ConvictionTier[] = [
  "ELITE",
  "STRONG",
  "MODERATE",
  "LEAN",
  "NO_PLAY",
];

// Map a letter grade ("A+", "A", "B", ...) to a generic tier when no
// market-specific tier (NRFI/YRFI) is available.
export function tierFromGrade(grade: string | null | undefined): ConvictionTier {
  switch ((grade ?? "").toUpperCase()) {
    case "A+":
      return "ELITE";
    case "A":
      return "STRONG";
    case "B":
      return "MODERATE";
    case "C":
      return "LEAN";
    default:
      return "NO_PLAY";
  }
}

// Map an NRFI/YRFI tier name ("LOCK" | "ELITE" | "STRONG" | ...) into
// the unified conviction tier. Side-aware overrides were dropped
// 2026-05-01 — STRONG is STRONG on either side.
export function tierFromNrfi(
  nrfiTier: string | null | undefined,
  // The `market` parameter is preserved for backwards-compatible
  // call sites; it has no effect on the returned tier.
  _market?: "NRFI" | "YRFI",
): ConvictionTier {
  const t = (nrfiTier ?? "").toUpperCase();
  if (t === "LOCK" || t === "ELITE") return "ELITE";
  if (t === "STRONG") return "STRONG";
  if (t === "MODERATE") return "MODERATE";
  if (t === "LEAN") return "LEAN";
  return "NO_PLAY";
}

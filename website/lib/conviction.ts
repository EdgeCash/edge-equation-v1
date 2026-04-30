// Single source of truth for the V4 Conviction system.
//
// Every pick on the site — regardless of sport or market — maps to exactly
// one tier here. The tier drives the color, the label, and the visual weight.
// If a tier isn't represented here, it doesn't get a color.

export type ConvictionTier =
  | "ELITE"
  | "STRONG_NRFI"
  | "STRONG_YRFI"
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
  STRONG_NRFI: {
    tier: "STRONG_NRFI",
    label: "Deep Green",
    longLabel: "Deep Green · Strong NRFI",
    description:
      "Strong NRFI / over-the-under read. Pitching and park lean toward a quiet first inning.",
    textClass: "text-conviction-strong",
    bgSoftClass: "bg-conviction-strongSoft",
    borderClass: "border-conviction-strong",
    dotClass: "bg-conviction-strong",
  },
  STRONG_YRFI: {
    tier: "STRONG_YRFI",
    label: "Red",
    longLabel: "Red · Strong YRFI",
    description:
      "Strong YRFI read. Lineup, weather, or pitcher fragility points to a run in the first.",
    textClass: "text-conviction-fade",
    bgSoftClass: "bg-conviction-fadeSoft",
    borderClass: "border-conviction-fade",
    dotClass: "bg-conviction-fade",
  },
  STRONG: {
    tier: "STRONG",
    label: "Strong",
    longLabel: "Strong",
    description:
      "Solid edge with grade A inputs. Not Elite, but the kind of play we run every day.",
    textClass: "text-conviction-strong",
    bgSoftClass: "bg-conviction-strongSoft",
    borderClass: "border-conviction-strong",
    dotClass: "bg-conviction-strong",
  },
  MODERATE: {
    tier: "MODERATE",
    label: "Moderate",
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
    label: "Lean",
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
    label: "No Play",
    longLabel: "No Play",
    description:
      "The model and the market agree. We pass — and saying that is the whole point.",
    textClass: "text-edge-textFaint",
    bgSoftClass: "bg-conviction-neutralSoft",
    borderClass: "border-edge-line",
    dotClass: "bg-edge-textFaint",
  },
};

// Ordered list for the visual key — Elite first, then strong directions,
// then everything quieter.
export const CONVICTION_ORDER: ConvictionTier[] = [
  "ELITE",
  "STRONG_NRFI",
  "STRONG_YRFI",
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

// Map an NRFI/YRFI tier ("LOCK" | "STRONG" | ...) plus a market_type
// ("NRFI" or "YRFI") into the unified conviction tier.
export function tierFromNrfi(
  nrfiTier: string | null | undefined,
  market: "NRFI" | "YRFI",
): ConvictionTier {
  const t = (nrfiTier ?? "").toUpperCase();
  if (t === "LOCK") return "ELITE";
  if (t === "STRONG") return market === "YRFI" ? "STRONG_YRFI" : "STRONG_NRFI";
  if (t === "MODERATE") return "MODERATE";
  if (t === "LEAN") return "LEAN";
  return "NO_PLAY";
}

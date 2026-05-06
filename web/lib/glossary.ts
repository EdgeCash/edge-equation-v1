/**
 * Single source of truth for the metric glossary.
 *
 * Keys are deliberately short (`edge`, `clv`, …) so component callers
 * don't need to memorise long identifiers. Each entry pairs the
 * display label with a one-paragraph definition tuned for novices —
 * the audit's "tooltips that explain without dumbing down" rule.
 */

export type GlossaryKey =
  | "edge"
  | "clv"
  | "brier"
  | "kelly"
  | "joint_prob"
  | "tier"
  | "model_prob"
  | "implied_prob"
  | "no_qualified"
  | "roi"
  | "hit_rate";


export interface GlossaryEntry {
  display: string;
  body: string;
}


export const GLOSSARY: Record<GlossaryKey, GlossaryEntry> = {
  edge: {
    display: "Edge",
    body:
      "Model's probability minus the de-vigged market probability, in "
      + "percentage points. Positive edge means the model thinks the "
      + "side is more likely than the book is pricing.",
  },
  clv: {
    display: "Closing-Line Value (CLV)",
    body:
      "How much the market moved toward our pick between when we "
      + "fired and when the line closed. Long-run profitability "
      + "tracks CLV more reliably than W/L does.",
  },
  brier: {
    display: "Brier score",
    body:
      "Mean squared error of the model's probability vs the actual "
      + "outcome (0 = perfect, 0.25 = coin flip). Lower is better. "
      + "Our publish gate is < 0.246.",
  },
  kelly: {
    display: "Kelly stake",
    body:
      "Bankroll fraction the Kelly criterion recommends given the "
      + "model's edge and the price. We bet half-Kelly to control "
      + "variance.",
  },
  joint_prob: {
    display: "Joint probability",
    body:
      "Correlation-adjusted probability that every leg in a parlay "
      + "hits, computed via Monte-Carlo simulation over a Gaussian "
      + "copula. Less optimistic than naively multiplying leg "
      + "probabilities.",
  },
  tier: {
    display: "Conviction tier",
    body:
      "Signal Elite / Strong / Moderate / Lean / No Signal. Tier "
      + "tells you how confident the model is; the unit chip tells "
      + "you how much to risk.",
  },
  model_prob: {
    display: "Model probability",
    body:
      "Engine's calibrated probability the side hits. Compare to the "
      + "implied market probability to see the edge.",
  },
  implied_prob: {
    display: "Implied probability",
    body:
      "Probability the book's price implies after vig is removed by "
      + "summing the over/under sides and re-normalising.",
  },
  no_qualified: {
    display: "No qualified parlay",
    body:
      "When no leg combination clears the strict gates (3–6 legs, "
      + "≥4pp edge or ELITE tier, EV > 0 after vig), the engine "
      + "publishes nothing rather than forcing a ticket.",
  },
  roi: {
    display: "ROI",
    body:
      "Total units returned divided by units staked. Positive ROI "
      + "after vig means the engine is grinding edge over the long "
      + "run.",
  },
  hit_rate: {
    display: "Hit rate",
    body:
      "Wins / (wins + losses). Useful as a sanity check but less "
      + "informative than CLV — a 60% hit rate at the wrong price "
      + "still loses money long-term.",
  },
};

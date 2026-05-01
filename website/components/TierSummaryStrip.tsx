import {
  TIER_ORDER, TIER_LABEL, type SummaryBucket, type Tier,
  bucketsByTierAcrossEngines, type SummaryFile,
} from "@/lib/track-record";


type Props = { summary: SummaryFile };


// Map tier → tailwind conviction tokens. ELITE = electric blue (the
// only "loud" color), STRONG = deep green, MODERATE = amber, LEAN =
// neutral slate. Matches the V4 brand palette in tailwind.config.js.
const TIER_CLASSES: Record<Tier, { border: string; eyebrow: string }> = {
  ELITE:    { border: "border-t-conviction-elite",    eyebrow: "text-conviction-elite" },
  STRONG:   { border: "border-t-conviction-strong",   eyebrow: "text-conviction-strong" },
  MODERATE: { border: "border-t-conviction-moderate", eyebrow: "text-conviction-moderate" },
  LEAN:     { border: "border-t-conviction-neutral",  eyebrow: "text-conviction-neutral" },
};


/**
 * Top-of-page strip — one card per tier, shown in conviction order
 * (ELITE → LEAN). All-engines roll-up: every engine's ELITE picks
 * combine into one card. Tiers with n_settled=0 render as "No
 * settled picks yet" rather than fake 0% — sample-size honesty is
 * the point of the public ledger.
 */
export default function TierSummaryStrip({ summary }: Props) {
  const buckets = bucketsByTierAcrossEngines(summary);
  const byTier = new Map<Tier, SummaryBucket>();
  for (const b of buckets) byTier.set(b.tier, b);

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {TIER_ORDER.map((tier) => {
        const b = byTier.get(tier);
        const styles = TIER_CLASSES[tier];
        const label = TIER_LABEL[tier];
        const hasData = b !== undefined && b.n_settled > 0;
        return (
          <div
            key={tier}
            className={`rounded-sm border border-edge-line bg-ink-900/80 p-4 border-t-[3px] ${styles.border}`}
          >
            <div className="flex items-baseline justify-between">
              <span
                className={`font-mono text-[10px] font-semibold uppercase tracking-[0.24em] ${styles.eyebrow}`}
              >
                {label}
              </span>
              {hasData && (
                <span className="font-mono text-[10px] text-edge-textFaint">
                  n={b!.n_settled}
                </span>
              )}
            </div>
            <div className="mt-3">
              {hasData ? (
                <>
                  <div className="font-display text-3xl tracking-tightest text-edge-text">
                    {b!.hit_pct.toFixed(1)}%
                  </div>
                  <div className="mt-1 text-[11px] text-edge-textDim">
                    {b!.wins}W · {b!.losses}L
                    {b!.pushes > 0 ? ` · ${b!.pushes}P` : ""}
                  </div>
                  <div className="mt-1 text-[11px] text-edge-textFaint">
                    {b!.units_won >= 0 ? "+" : ""}
                    {b!.units_won.toFixed(2)}u
                  </div>
                </>
              ) : (
                <div className="text-xs leading-tight text-edge-textFaint">
                  No settled picks yet.
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

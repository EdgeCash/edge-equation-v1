// Compact pill that signals the conviction tier of a single pick.
// Reads from lib/conviction so colors and labels stay consistent across pages.

import { CONVICTION, type ConvictionTier } from "@/lib/conviction";

type ConvictionBadgeProps = {
  tier: ConvictionTier;
  size?: "sm" | "md";
  showLabel?: boolean;
  className?: string;
};

export default function ConvictionBadge({
  tier,
  size = "sm",
  showLabel = true,
  className = "",
}: ConvictionBadgeProps) {
  const meta = CONVICTION[tier];
  const padding = size === "md" ? "px-3 py-1" : "px-2 py-0.5";
  const text = size === "md" ? "text-[11px]" : "text-[10px]";
  const isElite = tier === "ELITE";

  return (
    <span
      className={[
        "inline-flex items-center gap-2 rounded-sm border font-mono uppercase tracking-[0.18em]",
        padding,
        text,
        meta.borderClass,
        meta.bgSoftClass,
        meta.textClass,
        isElite ? "shadow-elite-glow" : "",
        className,
      ].join(" ")}
    >
      <span
        className={[
          "inline-block h-1.5 w-1.5 rounded-full",
          meta.dotClass,
          isElite ? "animate-pulse" : "",
        ].join(" ")}
        aria-hidden="true"
      />
      {showLabel && meta.label}
    </span>
  );
}

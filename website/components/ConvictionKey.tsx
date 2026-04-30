// Visual legend explaining every conviction tier on the site.
// Designed to live on the homepage, the Daily Edge page, and the Learn page.

import { CONVICTION, CONVICTION_ORDER } from "@/lib/conviction";

type ConvictionKeyProps = {
  variant?: "full" | "compact";
  className?: string;
};

export default function ConvictionKey({
  variant = "full",
  className = "",
}: ConvictionKeyProps) {
  if (variant === "compact") {
    return (
      <div
        className={
          "flex flex-wrap items-center gap-x-5 gap-y-2 " + className
        }
      >
        {CONVICTION_ORDER.map((tier) => {
          const meta = CONVICTION[tier];
          return (
            <div key={tier} className="flex items-center gap-2">
              <span
                className={"inline-block h-2 w-2 rounded-full " + meta.dotClass}
                aria-hidden="true"
              />
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
                {meta.label}
              </span>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div
      className={
        "border border-edge-line rounded-sm bg-ink-900/60 backdrop-blur " +
        className
      }
    >
      <div className="border-b border-edge-line px-5 py-3 flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-[0.28em] text-edge-accent">
          Conviction Key
        </div>
        <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-edge-textFaint">
          One system. Every pick.
        </div>
      </div>
      <ul className="divide-y divide-edge-line">
        {CONVICTION_ORDER.map((tier) => {
          const meta = CONVICTION[tier];
          const isElite = tier === "ELITE";
          return (
            <li
              key={tier}
              className="grid grid-cols-[auto_1fr] sm:grid-cols-[auto_220px_1fr] gap-4 items-start px-5 py-4"
            >
              <span
                className={[
                  "mt-1 inline-block h-3 w-3 rounded-full",
                  meta.dotClass,
                  isElite ? "shadow-elite-glow" : "",
                ].join(" ")}
                aria-hidden="true"
              />
              <div>
                <div
                  className={
                    "font-display text-lg tracking-tightest " + meta.textClass
                  }
                >
                  {meta.longLabel}
                </div>
                <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-edge-textFaint mt-1">
                  Tier · {meta.tier.replace("_", " ")}
                </div>
              </div>
              <p className="text-sm text-edge-textDim leading-relaxed sm:col-span-1 col-span-2">
                {meta.description}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

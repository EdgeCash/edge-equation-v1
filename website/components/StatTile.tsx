// A small KPI block: label above, monospaced value below.
// Used across the daily edge card and the grade-history dashboard.

type StatTileProps = {
  label: string;
  value: string;
  subValue?: string;
  className?: string;
};

export default function StatTile({
  label,
  value,
  subValue,
  className = "",
}: StatTileProps) {
  return (
    <div
      className={
        "relative border border-edge-line rounded-sm p-4 bg-ink-900/40 " +
        "hover:border-edge-accent/40 transition-colors " +
        className
      }
    >
      {/* Top hairline — implied data-block top */}
      <div className="absolute inset-x-3 top-0 h-px bg-edge-accent/30" />
      <div className="annotation flex items-center gap-2">
        <span className="text-edge-accent">·</span>
        <span>{label}</span>
      </div>
      <div className="mt-2 font-mono tabular-nums text-edge-text text-2xl">
        {value}
      </div>
      {subValue && (
        <div className="mt-1 font-mono text-[11px] text-edge-textDim tabular-nums">
          {subValue}
        </div>
      )}
    </div>
  );
}

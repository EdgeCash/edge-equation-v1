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
    <div className={"border border-edge-line rounded-sm p-4 " + className}>
      <div className="text-[10px] uppercase tracking-[0.2em] text-edge-textDim">
        {label}
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

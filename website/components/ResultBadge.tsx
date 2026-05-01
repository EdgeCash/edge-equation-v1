import type { ResultLabel } from "@/lib/track-record";


type Props = { result: ResultLabel };


/** W / L / Push / Pending pill. Honest naming — Pending is its own
 * state, not "TBD" or hidden. Operators want to see open picks too.
 * Colors lean on the V4 conviction palette so wins look "strong"
 * and losses look "fade". */
export default function ResultBadge({ result }: Props) {
  const styles: Record<ResultLabel, { className: string; label: string }> = {
    W: {
      className: "bg-conviction-strongSoft text-conviction-strong",
      label: "W",
    },
    L: {
      className: "bg-conviction-fadeSoft text-conviction-fade",
      label: "L",
    },
    Push: {
      className: "bg-conviction-neutralSoft text-edge-textDim",
      label: "Push",
    },
    Pending: {
      className: "bg-conviction-eliteSoft text-conviction-elite",
      label: "Pending",
    },
  };
  const s = styles[result];
  return (
    <span
      className={`inline-flex h-6 min-w-[36px] items-center justify-center rounded-sm px-2 font-mono text-[10px] font-semibold uppercase tracking-wider ${s.className}`}
    >
      {s.label}
    </span>
  );
}

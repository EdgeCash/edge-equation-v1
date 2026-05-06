/**
 * Lightweight metric tooltip primitive.
 *
 * Pure CSS hover/focus tooltip — zero JS state, keyboard-accessible
 * via the underlying `<button>`'s focus ring + the popover that
 * persists while the trigger is focused. Sized for inline use next
 * to a metric label or a number.
 *
 * Definitions live in `lib/glossary.ts` so a copy edit lands in one
 * place.
 */

import { GLOSSARY, GlossaryKey } from "../lib/glossary";


interface MetricTipProps {
  term: GlossaryKey;
  /** Optional override for the visible label. Defaults to the
   * glossary entry's `display`. */
  label?: string;
  /** Render the term as plain text (no underline, no button) — useful
   * inside table headers where the dotted underline would be noisy. */
  inline?: boolean;
}


export function MetricTip({ term, label, inline = false }: MetricTipProps) {
  const entry = GLOSSARY[term];
  const text = label ?? entry.display;
  const triggerCls = inline
    ? "cursor-help"
    : "cursor-help underline decoration-dotted decoration-chalk-500/60 underline-offset-4";
  return (
    <span className="relative group inline-block">
      <button
        type="button"
        tabIndex={0}
        className={`${triggerCls} bg-transparent border-0 p-0 text-inherit font-inherit focus:outline-none`}
        aria-describedby={`tip-${term}`}
      >
        {text}
      </button>
      <span
        id={`tip-${term}`}
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 w-64 -translate-x-1/2 rounded border border-chalkboard-600/80 bg-chalkboard-950/95 p-3 text-xs leading-snug text-chalk-100 shadow-[0_8px_32px_rgba(0,0,0,0.5)] opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        <span className="block font-mono text-[10px] uppercase tracking-wider text-elite mb-1">
          {entry.display}
        </span>
        {entry.body}
      </span>
    </span>
  );
}

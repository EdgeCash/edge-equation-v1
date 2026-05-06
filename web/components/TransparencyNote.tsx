/**
 * Audit-locked transparency note rendered on every page that ships
 * model output. Mirrors the same string the parlay engines emit in
 * the daily feed.
 */
export function TransparencyNote() {
  return (
    <section className="max-w-7xl mx-auto px-4 sm:px-6 pb-12 pt-2">
      <p className="text-xs text-chalk-500 leading-relaxed border-t border-chalkboard-700/60 pt-4">
        All suggestions are model outputs. Edges and CLV are tracked
        publicly; click any name in this card to open the full data
        view. <span className="text-chalk-300 italic">Facts. Not Feelings.</span>
      </p>
    </section>
  );
}

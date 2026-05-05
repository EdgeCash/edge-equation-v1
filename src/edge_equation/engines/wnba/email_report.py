from typing import List
from dataclasses import dataclass

from edge_equation.utils.logger import log
from edge_equation.posting.formatters import format_number
from .schema import Output, Market
from .run_daily import WNBARunner


@dataclass
class WNBAEmailReport:
    """
    Daily WNBA email body generator.
    Mirrors the NRFI email report structure.
    """

    def build(self, picks: List[Output]) -> str:
        if not picks:
            return "WNBA Daily Report\n\nNo qualifying plays today."

        lines = []
        lines.append("WNBA Daily Report")
        lines.append("Facts. Not Feelings.\n")

        # Group by market for readability
        grouped = self._group_by_market(picks)

        for market, items in grouped.items():
            lines.append(f"=== {market.upper()} ===")
            for o in items:
                lines.append(self._format_pick(o))
            lines.append("")  # spacing

        # Footer
        lines.append("---")
        lines.append("Deterministic + ML hybrid engine.")
        lines.append("Outputs are probabilities, projections, and edges — not betting advice.")
        lines.append("© Edge Equation")

        return "\n".join(lines)

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _group_by_market(self, picks: List[Output]):
        grouped = {}
        for p in picks:
            key = p.market.value
            grouped.setdefault(key, []).append(p)
        return grouped

    def _format_pick(self, o: Output) -> str:
        proj = format_number(o.projection)
        line = format_number(o.line)
        edge = format_number(o.edge)
        conf = format_number(o.confidence)

        base = f"{o.player} ({o.team} vs {o.opponent}) — {o.market.value.upper()}"
        stats = f"Proj: {proj} | Line: {line} | Edge: {edge} | Conf: {conf}"
        grade = f"Grade: {o.grade} | Model: {o.model_version}"

        return f"{base}\n  {stats}\n  {grade}"


# ---------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------

def main():
    log.info("Generating WNBA daily email report...")
    runner = WNBARunner()
    picks = runner.run()
    report = WNBAEmailReport().build(picks)
    print(report)


if __name__ == "__main__":
    main()

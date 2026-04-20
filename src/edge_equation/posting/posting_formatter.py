"""
Posting formatter.

Structured card payloads. No graphics, no network. Pure dict output.
"""
from decimal import Decimal
from typing import Iterable, Optional

from edge_equation.engine.pick_schema import Pick


TAGLINE = "Facts. Not Feelings."


CARD_TEMPLATES = {
    "daily_edge": {"headline": "Daily Edge", "subhead": "Today's model-graded plays."},
    "evening_edge": {"headline": "Evening Edge", "subhead": "Late slate picks from the engine."},
    "overseas_edge": {"headline": "Overseas Edge", "subhead": "International slate -- KBO, NPB, and global soccer."},
    "highlighted_game": {"headline": "Highlighted Game", "subhead": "Tonight's model focus."},
    "model_highlight": {"headline": "Model Highlight", "subhead": "Top-graded play from the engine. Hype-free."},
    "sharp_signal": {"headline": "Sharp Signal", "subhead": "Where the model and the market disagree most."},
    "the_outlier": {"headline": "The Outlier", "subhead": "The play the model loves and the market hasn't caught."},
}


class PostingFormatter:

    @staticmethod
    def _best_grade(picks: list) -> str:
        if not picks:
            return "C"
        order = {"A+": 3, "A": 2, "B": 1, "C": 0}
        return max(picks, key=lambda p: order.get(p.grade, 0)).grade

    @staticmethod
    def _max_edge(picks: list):
        edges = [p.edge for p in picks if p.edge is not None]
        return max(edges) if edges else None

    @staticmethod
    def _max_kelly(picks: list):
        kellys = [p.kelly for p in picks if p.kelly is not None]
        return max(kellys) if kellys else None

    @staticmethod
    def build_card(
        card_type: str,
        picks: Iterable[Pick],
        generated_at: Optional[str] = None,
        headline_override: Optional[str] = None,
        subhead_override: Optional[str] = None,
    ) -> dict:
        if card_type not in CARD_TEMPLATES:
            raise ValueError(
                f"Unknown card_type: {card_type}. "
                f"Valid: {sorted(CARD_TEMPLATES.keys())}"
            )
        picks_list = list(picks)
        template = CARD_TEMPLATES[card_type]

        summary = {
            "grade": PostingFormatter._best_grade(picks_list),
            "edge": PostingFormatter._max_edge(picks_list),
            "kelly": PostingFormatter._max_kelly(picks_list),
        }
        summary["edge"] = str(summary["edge"]) if summary["edge"] is not None else None
        summary["kelly"] = str(summary["kelly"]) if summary["kelly"] is not None else None

        return {
            "card_type": card_type,
            "headline": headline_override or template["headline"],
            "subhead": subhead_override or template["subhead"],
            "picks": [p.to_dict() for p in picks_list],
            "summary": summary,
            "tagline": TAGLINE,
            "generated_at": generated_at,
        }

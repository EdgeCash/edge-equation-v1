"""
X (Twitter) post formatters.

Two styles:
- StandardFormatter: 280-char hard cap (for fallback, non-Premium accounts,
  or cross-posting to 280-char platforms).
- PremiumFormatter: long-form, rich formatting that takes advantage of the
  X Premium 25,000-character ceiling. Every pick gets its own multi-line
  block; edges and Kelly are rendered as percentages; grades get badges;
  section dividers separate headline / picks / footer.

Both formatters are pure functions of the card dict produced by
posting.posting_formatter. They emit plain UTF-8 text -- X renders the
emoji and box-drawing characters without any special handling.
"""
from decimal import Decimal
from typing import List, Optional


STANDARD_MAX_LEN = 280
PREMIUM_MAX_LEN = 25000

GRADE_BADGE = {
    "A+": "🔥 A+",
    "A":  "⭐ A",
    "B":  "✅ B",
    "C":  "➖ C",
    "D":  "⚠️ D",
    "F":  "❌ F",
}

CARD_TYPE_ICON = {
    "daily_edge":       "🎯",
    "evening_edge":     "🌙",
    "overseas_edge":    "🌏",
    "highlighted_game": "📺",
    "model_highlight":  "🏆",
    "sharp_signal":     "📊",
    "the_outlier":      "⚡",
}

DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━"


def _decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _pct(value, places: int = 2) -> str:
    d = _decimal(value)
    if d is None:
        return "—"
    pct = d * Decimal('100')
    q = Decimal('1').scaleb(-places)
    return f"{pct.quantize(q)}%"


def _american(odds) -> str:
    if odds is None:
        return "—"
    try:
        o = int(odds)
    except (TypeError, ValueError):
        return str(odds)
    return f"{o:+d}"


def _line_label(line: dict) -> str:
    if not line:
        return ""
    number = line.get("number")
    odds = line.get("odds")
    am = _american(odds)
    if number is not None and number != "":
        return f"{number} @ {am}"
    return am


class StandardFormatter:
    """
    280-char cap formatter. Preserves legacy behavior:
    - Single line headline with card-type icon
    - One bullet per pick, up to the first 2 picks
    - Truncate with ellipsis if still over 280 after assembly
    """

    @staticmethod
    def format_card(card: dict, max_len: int = STANDARD_MAX_LEN) -> str:
        icon = CARD_TYPE_ICON.get(card.get("card_type", ""), "🎯")
        headline = card.get("headline") or ""
        picks = card.get("picks") or []
        tagline = card.get("tagline") or ""

        lines = [f"{icon} {headline}"]
        for p in picks[:2]:
            market = p.get("market_type") or ""
            selection = p.get("selection") or "?"
            grade = GRADE_BADGE.get(p.get("grade", ""), p.get("grade", ""))
            edge = p.get("edge")
            edge_str = f" · edge {_pct(edge)}" if edge else ""
            lines.append(f"• {market}: {selection} [{grade}]{edge_str}")
        if tagline:
            lines.append(tagline)

        text = "\n".join(lines)
        if len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"
        return text


class PremiumFormatter:
    """
    Long-form formatter for X Premium accounts (25K char ceiling).

    Structure:
      <icon> <HEADLINE>
      <subhead>
      ━━━━━━━━━━━━━━━━━━━━━━━━━
      [summary block]
      ━━━━━━━━━━━━━━━━━━━━━━━━━
      PICK 1 / N
        market · selection
        line/odds · grade badge
        fair prob · edge · kelly
      ━━━━━━━━━━━━━━━━━━━━━━━━━
      ... more picks ...
      ━━━━━━━━━━━━━━━━━━━━━━━━━
      <tagline>
      <generated_at footer>
    """

    @staticmethod
    def _summary_block(card: dict) -> List[str]:
        summary = card.get("summary") or {}
        grade = summary.get("grade") or "C"
        edge = summary.get("edge")
        kelly = summary.get("kelly")
        picks = card.get("picks") or []
        n = len(picks)
        lines = [
            f"Slate: {n} pick{'s' if n != 1 else ''}",
            f"Top grade: {GRADE_BADGE.get(grade, grade)}",
        ]
        if edge is not None:
            lines.append(f"Max edge: {_pct(edge)}")
        if kelly is not None:
            lines.append(f"Max Kelly: {_pct(kelly)}")
        return lines

    @staticmethod
    def _pick_block(pick: dict, index: int, total: int) -> List[str]:
        market = pick.get("market_type") or ""
        selection = pick.get("selection") or "?"
        grade = pick.get("grade", "") or ""
        badge = GRADE_BADGE.get(grade, grade)
        line = pick.get("line") or {}
        line_str = _line_label(line)
        sport = pick.get("sport") or ""
        game_id = pick.get("game_id")

        lines = [
            f"📌 Pick {index + 1}/{total}",
            f"  {sport} · {market}",
            f"  {selection}  ·  {line_str}",
            f"  {badge}",
        ]

        fair_prob = pick.get("fair_prob")
        expected_value = pick.get("expected_value")
        edge = pick.get("edge")
        kelly = pick.get("kelly")
        stat_parts = []
        if fair_prob is not None:
            stat_parts.append(f"fair {_pct(fair_prob)}")
        if expected_value is not None:
            stat_parts.append(f"EV {expected_value}")
        if edge is not None:
            stat_parts.append(f"edge {_pct(edge)}")
        if kelly is not None:
            stat_parts.append(f"Kelly {_pct(kelly)}")
        if stat_parts:
            lines.append("  " + "  ·  ".join(stat_parts))

        hfa = pick.get("hfa_value")
        halflife = pick.get("decay_halflife_days")
        ctx_parts = []
        if hfa is not None:
            ctx_parts.append(f"HFA {hfa}")
        if halflife is not None:
            ctx_parts.append(f"decay τ½ {halflife}d")
        if ctx_parts:
            lines.append("  " + "  ·  ".join(ctx_parts))

        if game_id:
            lines.append(f"  game: {game_id}")
        return lines

    @staticmethod
    def format_card(card: dict, max_len: int = PREMIUM_MAX_LEN) -> str:
        icon = CARD_TYPE_ICON.get(card.get("card_type", ""), "🎯")
        headline = card.get("headline") or ""
        subhead = card.get("subhead") or ""
        picks = card.get("picks") or []
        tagline = card.get("tagline") or ""
        generated_at = card.get("generated_at") or ""

        out: List[str] = []
        out.append(f"{icon} {headline.upper()}")
        if subhead:
            out.append(subhead)
        out.append(DIVIDER)
        out.extend(PremiumFormatter._summary_block(card))
        out.append(DIVIDER)
        for i, p in enumerate(picks):
            out.extend(PremiumFormatter._pick_block(p, i, len(picks)))
            out.append(DIVIDER)
        if tagline:
            out.append(tagline)
        if generated_at:
            out.append(f"generated {generated_at}")

        text = "\n".join(out)
        if len(text) > max_len:
            text = text[: max_len - 1].rstrip() + "…"
        return text


def format_card(card: dict, style: str = "premium", max_len: Optional[int] = None) -> str:
    """
    Convenience factory:
    - style="premium" (default) uses PremiumFormatter with 25K cap
    - style="standard" uses StandardFormatter with 280 cap
    """
    if style == "premium":
        return PremiumFormatter.format_card(card, max_len or PREMIUM_MAX_LEN)
    if style == "standard":
        return StandardFormatter.format_card(card, max_len or STANDARD_MAX_LEN)
    raise ValueError(f"Unknown style {style!r}; expected 'premium' or 'standard'.")

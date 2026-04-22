"""
Premium Daily email body renderer.

Plain-text, multi-section, no emoji/divider flash. Everything a
subscriber wants for the day in one email -- brand stays "Facts Not
Feelings" throughout; no tout language.

Sections (in order):
  1. Header (date, tagline)
  2. Major Variance Signal banner (only when any Grade-A+ pick cleared
     the strict MVS thresholds)
  3. Yesterday's Ledger recap (cross-slot summary of what we posted
     publicly yesterday)
  4. Daily Edge full slate: every A+ / A / A- pick with edge + Kelly
     visible (premium is not public_mode, so the numbers stay on)
  5. Spotlight deep dive: the day's trending game picks
  6. Player Prop Projections: brand-exact pipe table, same format as
     the free card -- no "DFS" or app mentions
  7. Parlay of the Day: 3 legs from distinct games + combined odds
  8. Engine Health: yesterday's hit rate + win/loss/push count

Beta test destination is controlled by the standard SMTP_* env vars;
EMAIL_TO defaults to ProfessorEdgeCash@gmail.com at the workflow
level. To broaden distribution later, point EMAIL_TO at a list
relay / transactional sender -- no code change needed.

This view is NEVER posted to X; the premium-daily CLI subcommand
emails it via SMTP.
"""
from decimal import Decimal
from typing import Iterable, List, Optional

from edge_equation.engine.major_variance import META_KEY as MVS_META_KEY, SIGNAL_LABEL as MVS_LABEL


# Transparent one-time note the first occurrence of the MVS in any given
# render. Keeps the tone factual / analytical, never tout-like.
_MVS_INTRO = (
    "This is a rare Major Variance Signal -- the model has detected an "
    "unusually strong alignment between data and projection."
)


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _pct(v, places: int = 2) -> str:
    d = _dec(v)
    if d is None:
        return "--"
    q = Decimal("1").scaleb(-places)
    return f"{(d * Decimal('100')).quantize(q)}%"


def _american(odds) -> str:
    if odds is None or odds == "":
        return "--"
    try:
        n = int(odds)
    except (TypeError, ValueError):
        return str(odds)
    return f"+{n}" if n > 0 else str(n)


def _decimal_from_american(odds) -> Optional[Decimal]:
    try:
        n = int(odds)
    except (TypeError, ValueError):
        return None
    if n > 0:
        return Decimal("1") + Decimal(n) / Decimal("100")
    return Decimal("1") + Decimal("100") / Decimal(-n)


def _render_pick_line(p: dict) -> str:
    sport = p.get("sport") or ""
    market = p.get("market_type") or ""
    selection = p.get("selection") or "?"
    grade = p.get("grade") or "?"
    line = p.get("line") or {}
    odds = _american(line.get("odds"))
    number = line.get("number")
    line_part = f"{number} @ {odds}" if number not in (None, "") else odds
    parts = [f"[{grade}]", f"{sport} · {market}", f"{selection}  ({line_part})"]
    edge = p.get("edge")
    kelly = p.get("kelly")
    stat_bits = []
    if edge is not None:
        stat_bits.append(f"edge {_pct(edge)}")
    if kelly is not None:
        stat_bits.append(f"Kelly {_pct(kelly)}")
    if stat_bits:
        parts.append(" · ".join(stat_bits))
    return "  ".join(parts)


def _render_parlay_block(parlay: List[dict]) -> List[str]:
    if not parlay:
        return ["(no parlay legs qualified today)"]
    combined: Optional[Decimal] = None
    for leg in parlay:
        dec_odds = _decimal_from_american((leg.get("line") or {}).get("odds"))
        if dec_odds is None:
            combined = None
            break
        combined = dec_odds if combined is None else combined * dec_odds
    lines: List[str] = []
    for i, leg in enumerate(parlay, 1):
        lines.append(f"  Leg {i}: {_render_pick_line(leg)}")
    if combined is not None:
        amer = combined - Decimal("1")
        if amer >= Decimal("1"):
            american = f"+{int((amer * Decimal('100')).quantize(Decimal('1')))}"
        else:
            american = str(int((Decimal("-100") / amer).quantize(Decimal("1"))))
        lines.append(
            f"  Combined: {combined.quantize(Decimal('0.01'))}x  ({american})"
        )
    return lines


def _render_engine_health(health: Optional[dict]) -> List[str]:
    if not health:
        return ["(no engine-health data yet)"]
    wins = int(health.get("wins", 0))
    losses = int(health.get("losses", 0))
    pushes = int(health.get("pushes", 0))
    n = int(health.get("n", wins + losses + pushes))
    rate = _pct(health.get("hit_rate"), places=1)
    return [
        f"  Record:    {wins}-{losses}-{pushes}  ({n} settled)",
        f"  Hit rate:  {rate}",
    ]


def _mvs_picks(picks: List[dict]) -> List[dict]:
    """Return the subset of picks whose metadata carries the MVS flag."""
    tagged: List[dict] = []
    for p in picks:
        meta = p.get("metadata") or {}
        if meta.get(MVS_META_KEY):
            tagged.append(p)
    return tagged


def _render_mvs_block(tagged: List[dict]) -> List[str]:
    """Factual, non-tout rendering of each firing MVS pick. Includes the
    one-time transparent intro note only on the first call per render
    (caller passes tagged only when at least one fires)."""
    lines: List[str] = []
    lines.append("=== MAJOR VARIANCE SIGNAL ===")
    lines.append(_MVS_INTRO)
    lines.append("")
    for p in tagged:
        sport = p.get("sport") or ""
        market = p.get("market_type") or ""
        selection = p.get("selection") or "?"
        line = p.get("line") or {}
        odds = _american(line.get("odds"))
        number = line.get("number")
        line_part = f"{number} @ {odds}" if number not in (None, "") else odds
        edge = p.get("edge")
        kelly = p.get("kelly")
        meta = p.get("metadata") or {}
        read = (meta.get("read_notes") or "").strip()
        if not read:
            read = "No analytical delta recorded."
        lines.append(f"  {MVS_LABEL}")
        lines.append(f"    {sport} · {market}  ({selection}, {line_part})")
        lines.append(
            f"    EE Projection: Grade A+  (Edge +{_pct(edge)})"
        )
        lines.append(
            f"    Kelly Suggestion: {_pct(kelly)} of bankroll (Half-Kelly)"
        )
        lines.append(f"    Read: {read}")
        lines.append("")
    return lines


def _render_spotlight_block(spot: Optional[dict]) -> List[str]:
    """Surface the trending game's picks. Factual tone, same `[grade]
    sport · market ...` row format as the full-slate block so the
    reader parses it without a mode switch."""
    if not spot or not spot.get("picks"):
        return ["  (no single game crossed the Spotlight bar today)"]
    game_id = spot.get("game_id") or ""
    sport = spot.get("sport") or ""
    header = f"  {sport}  ·  {game_id}" if game_id else f"  {sport}"
    lines = [header]
    for p in spot["picks"]:
        lines.append(f"  {_render_pick_line(p)}")
    return lines


def format_premium_daily(card: dict) -> str:
    """Render the premium_daily card as a plain-text email body."""
    headline = card.get("headline") or "Premium Daily Edge"
    subhead = card.get("subhead") or ""
    generated = card.get("generated_at") or ""
    date_part = generated.split("T", 1)[0] if generated else ""
    tagline = card.get("tagline") or ""

    picks = card.get("picks") or []
    parlay = card.get("parlay") or []
    health = card.get("engine_health")
    spotlight = card.get("spotlight")
    prop_section = (card.get("player_prop_projections") or {}).get("text") or ""
    recap_section = (card.get("daily_recap") or {}).get("text") or ""

    out: List[str] = []
    out.append(headline.upper())
    if date_part:
        out.append(date_part)
    if subhead:
        out.append(subhead)
    out.append("")

    # 1. Major Variance Signal banner (when firing). First thing the
    # subscriber sees if the rare signal fires.
    mvs_firing = _mvs_picks(picks)
    if mvs_firing:
        out.extend(_render_mvs_block(mvs_firing))

    # 2. Yesterday's Ledger recap -- what we posted publicly yesterday
    # with outcomes attached.
    if recap_section:
        out.append("=== YESTERDAY'S LEDGER ===")
        out.append(recap_section.rstrip())
        out.append("")

    # 3. Daily Edge: all A+ / A / A- picks with edge + Kelly.
    out.append(f"=== DAILY EDGE · A+ / A / A- ({len(picks)}) ===")
    if not picks:
        out.append("  (no qualifying picks today)")
    for p in picks:
        out.append(f"  {_render_pick_line(p)}")
    out.append("")

    # 4. Spotlight deep dive.
    out.append("=== SPOTLIGHT ===")
    out.extend(_render_spotlight_block(spotlight))
    out.append("")

    # 5. Player Prop Projections section. Brand-exact pipe format; same
    # as the free Daily Edge prop block, no DFS mentions, no Top-N
    # language. Edge + Kelly stay on adjacent sections (full slate +
    # parlay) so premium remains richer without renaming the section.
    if prop_section:
        out.append("=== PLAYER PROP PROJECTIONS ===")
        out.append(prop_section.rstrip())
        out.append("")

    # 6. Parlay of the Day.
    out.append("=== PARLAY OF THE DAY ===")
    out.extend(_render_parlay_block(parlay))
    out.append("")

    # 7. Engine Health.
    out.append("=== ENGINE HEALTH · YESTERDAY ===")
    out.extend(_render_engine_health(health))
    out.append("")

    if tagline:
        out.append(tagline)
    return "\n".join(out).rstrip() + "\n"

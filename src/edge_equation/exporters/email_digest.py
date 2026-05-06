"""Daily email digest — Resend-backed broadcast to public subscribers.

Separate path from `engines.nrfi.email_report` (which is the
operator's SMTP-driven NRFI alert). This module produces a single
multi-sport HTML + plaintext digest from the unified daily feed
and sends it to every subscriber on the configured Resend
audience.

Configuration (all env vars; absence = no-op so the daily
pipeline never fails on a missing key):

  EDGE_FEATURE_EMAIL_DIGEST     "on" to enable the daily send
  RESEND_API_KEY                Resend API key
  RESEND_AUDIENCE_ID            Audience to broadcast to
  RESEND_FROM_ADDRESS           e.g. "Edge Equation <picks@edgeequation.com>"
  RESEND_REPLY_TO               optional reply-to address

Design rules:

  - Soft-fail every error path. The daily build never crashes
    because email send hit a transient API error.
  - Plain `httpx` over the Resend REST API — no new SDK dep.
  - Honest "Picks shown only for games not yet started" framing
    matches the failsafe footer the website renders.
  - "Facts. Not Feelings." tone in body copy. No hype, no
    betting urgings, no clickbait subjects.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


RESEND_BASE_URL = "https://api.resend.com"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DigestConfig:
    api_key: Optional[str]
    audience_id: Optional[str]
    from_address: Optional[str]
    reply_to: Optional[str]
    enabled: bool

    @property
    def is_send_ready(self) -> bool:
        return bool(
            self.enabled
            and self.api_key
            and self.audience_id
            and self.from_address
        )


def load_config(env: Optional[dict[str, str]] = None) -> DigestConfig:
    e = env if env is not None else os.environ
    raw_flag = (e.get("EDGE_FEATURE_EMAIL_DIGEST") or "").strip().lower()
    return DigestConfig(
        api_key=(e.get("RESEND_API_KEY") or "").strip() or None,
        audience_id=(e.get("RESEND_AUDIENCE_ID") or "").strip() or None,
        from_address=(e.get("RESEND_FROM_ADDRESS") or "").strip() or None,
        reply_to=(e.get("RESEND_REPLY_TO") or "").strip() or None,
        enabled=raw_flag in {"1", "on", "true", "yes"},
    )


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Digest:
    subject: str
    html: str
    text: str


SPORT_LABELS: dict[str, str] = {
    "mlb": "MLB",
    "wnba": "WNBA",
    "nfl": "NFL",
    "ncaaf": "NCAAF",
}


SPORT_FEED_KEYS: list[tuple[str, str]] = [
    # (sport_key, feed key under unified daily JSON for picks)
    ("mlb", "picks"),
    ("wnba", "wnba"),
    ("nfl", "nfl"),
    ("ncaaf", "ncaaf"),
]


def build_digest(feed: dict[str, Any]) -> Digest:
    """Build the digest from today's unified daily feed dict."""
    target_date = str(feed.get("date") or "today")
    footer = str(feed.get("footer") or "").strip()
    sections: list[dict[str, Any]] = []

    for sport, key in SPORT_FEED_KEYS:
        picks = _picks_for_section(feed, sport, key)
        game_parlays, prop_parlays = _parlays_for_section(feed, sport, key)
        if not picks and not game_parlays and not prop_parlays:
            continue
        sections.append({
            "sport": sport,
            "label": SPORT_LABELS.get(sport, sport.upper()),
            "picks": picks[:8],          # cap visible rows; full list on web
            "n_picks": len(picks),
            "game_parlays": game_parlays[:2],
            "n_game_parlays": len(game_parlays),
            "prop_parlays": prop_parlays[:2],
            "n_prop_parlays": len(prop_parlays),
        })

    n_total_picks = sum(s["n_picks"] for s in sections)
    n_total_parlays = sum(
        s["n_game_parlays"] + s["n_prop_parlays"] for s in sections
    )

    subject = _build_subject(target_date, n_total_picks, n_total_parlays)
    html = _build_html(target_date, sections, footer=footer)
    text = _build_text(target_date, sections, footer=footer)
    return Digest(subject=subject, html=html, text=text)


def _picks_for_section(
    feed: dict, sport: str, key: str,
) -> list[dict]:
    if sport == "mlb":
        return list(feed.get("picks") or [])
    section = feed.get(key) or {}
    return list((section or {}).get("picks") or [])


def _parlays_for_section(
    feed: dict, sport: str, key: str,
) -> tuple[list[dict], list[dict]]:
    if sport == "mlb":
        parlays = (feed.get("parlays") or {})
    else:
        parlays = ((feed.get(key) or {}).get("parlays") or {})
    return (
        list((parlays or {}).get("game_results") or []),
        list((parlays or {}).get("player_props") or []),
    )


def _build_subject(target_date: str, n_picks: int, n_parlays: int) -> str:
    if n_picks == 0 and n_parlays == 0:
        return f"Edge Equation · {target_date} · No qualifying plays today"
    parts: list[str] = []
    if n_picks:
        parts.append(f"{n_picks} pick{'s' if n_picks != 1 else ''}")
    if n_parlays:
        parts.append(
            f"{n_parlays} parlay{'s' if n_parlays != 1 else ''}",
        )
    inner = " · ".join(parts)
    return f"Edge Equation · {target_date} · {inner}"


def _build_html(
    target_date: str,
    sections: list[dict],
    *,
    footer: str = "",
) -> str:
    """Minimalist email HTML — inline styles only (no external CSS)
    so every mail client renders consistently. Dark on light to
    stay legible in inboxes that override colors."""
    out: list[str] = [
        '<!doctype html><html><body style="margin:0;padding:0;'
        'background:#f5f7fa;font-family:-apple-system,BlinkMacSystemFont,'
        'Segoe UI,Roboto,sans-serif;color:#0f172a;">',
        '<table role="presentation" width="100%" cellspacing="0" '
        'cellpadding="0" style="max-width:640px;margin:24px auto;'
        'background:#ffffff;border-radius:8px;overflow:hidden;'
        'box-shadow:0 1px 3px rgba(15,23,42,0.08);">',
        # Header
        '<tr><td style="padding:24px 28px 8px;border-bottom:'
        '1px solid #e2e8f0;">',
        '<div style="font-size:14px;letter-spacing:0.04em;'
        'text-transform:uppercase;color:#64748b;">'
        f'Edge Equation · {_html(target_date)}</div>',
        '<div style="font-size:22px;font-weight:600;margin-top:4px;">'
        'Today&rsquo;s card</div>',
        '<div style="font-size:13px;color:#64748b;margin-top:6px;">'
        f'{_html(footer)}</div>',
        '</td></tr>',
    ]
    if not sections:
        out.append(
            '<tr><td style="padding:24px 28px;font-size:14px;'
            'color:#475569;">No qualifying plays today across any sport. '
            'The math says pass.</td></tr>',
        )
    for s in sections:
        out.append('<tr><td style="padding:20px 28px;border-top:'
                   '1px solid #f1f5f9;">')
        out.append(
            '<div style="font-size:11px;letter-spacing:0.08em;'
            'text-transform:uppercase;color:#64748b;font-weight:600;">'
            f'{_html(s["label"])}</div>',
        )
        if s["picks"]:
            out.append('<ul style="margin:8px 0 0;padding:0;'
                       'list-style:none;">')
            for p in s["picks"]:
                out.append(_pick_html_row(p))
            out.append('</ul>')
        if s["game_parlays"] or s["prop_parlays"]:
            for ticket in (
                list(s["game_parlays"]) + list(s["prop_parlays"])
            ):
                out.append(_parlay_html_row(ticket))
        out.append('</td></tr>')

    # Footer
    out.append(
        '<tr><td style="padding:18px 28px;background:#f8fafc;'
        'font-size:11px;color:#64748b;line-height:1.5;'
        'border-top:1px solid #e2e8f0;">'
        'Picks shown only for games not yet started · '
        'Facts. Not Feelings. · '
        '<a href="https://edgeequation.com" style="color:#0ea5e9;'
        'text-decoration:none;">Open the live site</a> for the '
        'full data view.'
        '</td></tr>',
    )
    out.append('</table></body></html>')
    return "".join(out)


def _pick_html_row(p: dict) -> str:
    selection = _html(str(p.get("selection") or ""))
    market = _html(str(p.get("market_type") or ""))
    edge = _format_edge(p.get("edge"))
    odds = _format_odds(p.get("line", {}).get("odds"))
    tier = _html(str(p.get("tier") or ""))
    return (
        '<li style="padding:8px 0;border-top:1px solid #f1f5f9;'
        'font-size:14px;">'
        f'<div style="color:#0f172a;">{selection}</div>'
        '<div style="font-size:11px;color:#64748b;margin-top:2px;'
        'font-family:ui-monospace,monospace;">'
        f'{market} · {edge} edge · {odds}'
        + (f" · tier {tier}" if tier and tier != "None" else "")
        + '</div></li>'
    )


def _parlay_html_row(t: dict) -> str:
    n_legs = t.get("n_legs", 0)
    odds = t.get("combined_american_odds", 0)
    legs = t.get("legs") or []
    leg_str = "<br>".join(
        _html(f"  · {leg.get('selection', '')}") for leg in legs
    )
    return (
        '<div style="margin-top:10px;padding:10px;background:#f8fafc;'
        'border:1px solid #e2e8f0;border-radius:6px;'
        'font-size:13px;">'
        f'<div style="font-weight:600;color:#0f172a;">'
        f'{n_legs}-leg parlay @ {_format_odds(odds)}</div>'
        f'<div style="margin-top:6px;color:#475569;font-size:12px;">'
        f'{leg_str}</div>'
        '</div>'
    )


def _build_text(
    target_date: str, sections: list[dict], *, footer: str = "",
) -> str:
    out: list[str] = [
        f"Edge Equation — {target_date}",
        "=" * 60,
        "",
    ]
    if footer:
        out.append(footer)
        out.append("")
    if not sections:
        out.append(
            "No qualifying plays today across any sport. "
            "The math says pass.",
        )
        out.append("")
    for s in sections:
        out.append(s["label"])
        out.append("-" * len(s["label"]))
        for p in s["picks"]:
            sel = str(p.get("selection") or "")
            market = str(p.get("market_type") or "")
            edge = _format_edge(p.get("edge"))
            odds = _format_odds(p.get("line", {}).get("odds"))
            out.append(f"  {sel}  ({market} · {edge} edge · {odds})")
        for ticket in (
            list(s["game_parlays"]) + list(s["prop_parlays"])
        ):
            n_legs = ticket.get("n_legs", 0)
            odds = ticket.get("combined_american_odds", 0)
            out.append(f"  {n_legs}-leg parlay @ {_format_odds(odds)}")
            for leg in ticket.get("legs") or []:
                out.append(f"    · {leg.get('selection', '')}")
        out.append("")
    out.append(
        "Picks shown only for games not yet started · "
        "Facts. Not Feelings.",
    )
    out.append("https://edgeequation.com")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Resend send
# ---------------------------------------------------------------------------


def send_digest(
    digest: Digest,
    config: DigestConfig,
    *,
    http_client=None,
) -> dict[str, Any]:
    """Send the digest via Resend's broadcast endpoint.

    Returns ``{"sent": bool, "skipped_reason": str | None,
    "broadcast_id": str | None, "error": str | None}``.
    """
    if not config.is_send_ready:
        reason = (
            "EDGE_FEATURE_EMAIL_DIGEST not 'on'"
            if not config.enabled
            else "RESEND_API_KEY / RESEND_AUDIENCE_ID / "
                 "RESEND_FROM_ADDRESS missing"
        )
        log.info("email_digest: skipping send — %s", reason)
        return {
            "sent": False, "skipped_reason": reason,
            "broadcast_id": None, "error": None,
        }

    payload: dict[str, Any] = {
        "audience_id": config.audience_id,
        "from": config.from_address,
        "subject": digest.subject,
        "html": digest.html,
        "text": digest.text,
    }
    if config.reply_to:
        payload["reply_to"] = config.reply_to

    try:
        if http_client is None:
            import httpx
            http_client = httpx.Client(timeout=15.0)
            owns_client = True
        else:
            owns_client = False
        try:
            resp = http_client.post(
                f"{RESEND_BASE_URL}/broadcasts",
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = _safe_json(resp)
            if resp.status_code >= 400:
                return {
                    "sent": False, "skipped_reason": None,
                    "broadcast_id": None,
                    "error": f"Resend HTTP {resp.status_code}: "
                             f"{(data or {}).get('message', resp.text[:200])}",
                }
            broadcast_id = (data or {}).get("id")
            # Trigger the actual send for the just-created broadcast.
            send_resp = http_client.post(
                f"{RESEND_BASE_URL}/broadcasts/{broadcast_id}/send",
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                json={},
            )
            if send_resp.status_code >= 400:
                send_data = _safe_json(send_resp)
                return {
                    "sent": False, "skipped_reason": None,
                    "broadcast_id": broadcast_id,
                    "error": f"Resend send HTTP {send_resp.status_code}: "
                             f"{(send_data or {}).get('message', send_resp.text[:200])}",
                }
            return {
                "sent": True, "skipped_reason": None,
                "broadcast_id": broadcast_id, "error": None,
            }
        finally:
            if owns_client:
                http_client.close()
    except Exception as e:  # pragma: no cover — defensive
        log.warning(
            "email_digest: send failed (%s): %s", type(e).__name__, e,
        )
        return {
            "sent": False, "skipped_reason": None,
            "broadcast_id": None,
            "error": f"{type(e).__name__}: {e}",
        }


# ---------------------------------------------------------------------------
# CLI hook — called by run_daily_all.py and operator dry-runs.
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Build + send today's email digest from the "
                    "unified daily feed.",
    )
    parser.add_argument(
        "--feed", default=None,
        help="Path to daily/latest.json (default: "
             "website/public/data/daily/latest.json)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build the digest and print to stdout; skip the send.",
    )
    parser.add_argument(
        "--out-html", default=None,
        help="Optional path to also write the HTML preview.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    feed_path = (
        Path(args.feed) if args.feed
        else _default_feed_path()
    )
    if not feed_path.exists():
        print(
            f"[email_digest] feed not found at {feed_path}; aborting.",
        )
        return 1
    feed = json.loads(feed_path.read_text())
    digest = build_digest(feed)

    if args.out_html:
        Path(args.out_html).write_text(digest.html, encoding="utf-8")
        print(f"[email_digest] HTML preview → {args.out_html}")

    if args.dry_run:
        print(f"--- DRY RUN ---\nSubject: {digest.subject}\n")
        print(digest.text)
        return 0

    config = load_config()
    result = send_digest(digest, config)
    if result.get("sent"):
        print(
            f"[email_digest] sent · broadcast_id={result.get('broadcast_id')}",
        )
        return 0
    if result.get("skipped_reason"):
        print(f"[email_digest] skipped — {result['skipped_reason']}")
        return 0
    print(f"[email_digest] FAILED — {result.get('error')}")
    return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_feed_path() -> Path:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "pyproject.toml").exists():
            return (
                ancestor / "website" / "public" / "data"
                / "daily" / "latest.json"
            )
    return (
        Path.cwd() / "website" / "public" / "data" / "daily" / "latest.json"
    )


def _safe_json(resp) -> Optional[dict]:
    try:
        return resp.json()
    except Exception:
        return None


def _format_edge(raw) -> str:
    if raw is None:
        return "—"
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return "—"
    if not (n == n and abs(n) < float("inf")):  # NaN / inf guard
        return "—"
    if abs(n) < 1:
        return f"{(n * 100):+.1f}pp"
    return f"{n:+.1f}pp"


def _format_odds(raw) -> str:
    if raw is None:
        return "—"
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return "—"
    if not (n == n and abs(n) < float("inf")):
        return "—"
    rounded = round(n)
    return f"+{rounded}" if rounded > 0 else str(rounded)


def _html(s: str) -> str:
    """Minimal HTML escape — no full template engine needed for the
    handful of fields we render."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


if __name__ == "__main__":
    import sys
    sys.exit(main())

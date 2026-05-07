"""Edge Equation daily content generator for @TheEdgeEquation.

Produces the three scheduled daily posts (10AM MLB projection, 1PM review,
4PM WNBA projection) as ready-to-post X text plus standalone Matplotlib
chart code. Pure projections and analysis only -- no betting language.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DISCLAIMER = (
    "Deterministic projections from our Edge Equation engine. "
    "For informational purposes only. Not advice or predictions of outcomes."
)

BRAND_FOOTER = "Powered by Edge Equation"

# Edge Equation visual identity (used by every chart).
BRAND_BG = "#0B2545"
BRAND_PANEL = "#13294B"
BRAND_PRIMARY = "#13C4A3"
BRAND_ACCENT = "#7FDBFF"
BRAND_TEXT = "#FFFFFF"
BRAND_MUTED = "#A9B7C6"
BRAND_FONT = "DejaVu Sans"


@dataclass
class Post:
    """A single ready-to-post output."""

    slot: str
    text: str
    chart_filename: str
    chart_code: str

    def to_dict(self) -> dict[str, str]:
        return {
            "slot": self.slot,
            "text": self.text,
            "chart_filename": self.chart_filename,
            "chart_code": self.chart_code,
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fmt_number(value: float | int, digits: int = 1) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = ["---"] * len(headers)
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(sep) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _chart_preamble(title: str) -> str:
    """Return the standard Matplotlib styling preamble used by every chart."""
    return f"""import matplotlib.pyplot as plt
from matplotlib import rcParams

BRAND_BG = "{BRAND_BG}"
BRAND_PANEL = "{BRAND_PANEL}"
BRAND_PRIMARY = "{BRAND_PRIMARY}"
BRAND_ACCENT = "{BRAND_ACCENT}"
BRAND_TEXT = "{BRAND_TEXT}"
BRAND_MUTED = "{BRAND_MUTED}"

rcParams["font.family"] = "{BRAND_FONT}"
rcParams["font.size"] = 11
rcParams["axes.edgecolor"] = BRAND_MUTED
rcParams["axes.labelcolor"] = BRAND_TEXT
rcParams["xtick.color"] = BRAND_TEXT
rcParams["ytick.color"] = BRAND_TEXT
rcParams["axes.titlecolor"] = BRAND_TEXT
rcParams["axes.titleweight"] = "bold"

fig, ax = plt.subplots(figsize=(9, 5.0625), dpi=200)
fig.patch.set_facecolor(BRAND_BG)
ax.set_facecolor(BRAND_PANEL)
ax.set_title({title!r}, pad=14)
"""


def _chart_footer(filename: str) -> str:
    """Return the closing block: branding footer, layout, save."""
    return f"""
ax.grid(True, axis="y", color=BRAND_MUTED, alpha=0.18, linewidth=0.7)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
for spine in ("left", "bottom"):
    ax.spines[spine].set_color(BRAND_MUTED)

fig.text(
    0.99, 0.015, "{BRAND_FOOTER}",
    ha="right", va="bottom",
    color=BRAND_PRIMARY, fontsize=10, fontweight="bold",
    family="{BRAND_FONT}",
)

fig.tight_layout(rect=(0, 0.04, 1, 1))
fig.savefig({filename!r}, facecolor=BRAND_BG, dpi=200, bbox_inches="tight")
"""


# ---------------------------------------------------------------------------
# 10 AM -- MLB Projection Post
# ---------------------------------------------------------------------------


def generate_mlb_projection_post(data: dict[str, Any]) -> Post:
    """Generate the 10:00 AM MLB projection post.

    Expected ``data`` keys:
      date, hook, why (list[str] of 3-5 sentences),
      game: {away, home, away_proj, home_proj, total_proj, market_total,
             first_pitch_local, park},
      prop: {player, team, market, line, projection, edge_pct},
      chart: {kind: "line"|"bar", title, x_label, y_label, x_values,
              y_values, line_value (optional), subject_label}
    """
    game = data["game"]
    prop = data["prop"]

    text = _build_mlb_text(data, game, prop)
    filename = f"mlb_projection_{data['date']}.png"
    chart_code = _build_player_chart_code(data["chart"], filename)

    return Post(slot="10:00 AM ET", text=text, chart_filename=filename, chart_code=chart_code)


def _build_mlb_text(data: dict[str, Any], game: dict[str, Any], prop: dict[str, Any]) -> str:
    why_lines = data.get("why", [])
    if not 3 <= len(why_lines) <= 5:
        raise ValueError("MLB 'why' must contain 3-5 sentences.")

    matchup = f"{game['away']} @ {game['home']}"
    proj_table = _markdown_table(
        ["Metric", "Engine Projection", "Market"],
        [
            [f"{game['away']} runs", _fmt_number(game["away_proj"], 2), "—"],
            [f"{game['home']} runs", _fmt_number(game["home_proj"], 2), "—"],
            [
                "Total runs",
                _fmt_number(game["total_proj"], 2),
                _fmt_number(game.get("market_total", 0.0), 1)
                if game.get("market_total") is not None
                else "—",
            ],
        ],
    )

    prop_table = _markdown_table(
        ["Player", "Market", "Line", "Engine Projection"],
        [[
            f"{prop['player']} ({prop['team']})",
            prop["market"],
            _fmt_number(prop["line"], 1),
            _fmt_number(prop["projection"], 2),
        ]],
    )

    parts = [
        DISCLAIMER,
        "",
        f"MLB Projection — {matchup}",
        f"First pitch: {game['first_pitch_local']} | Park: {game['park']}",
        "",
        data["hook"],
        "",
        "Why the engine is here:",
        " ".join(why_lines),
        "",
        "Game projection:",
        proj_table,
        "",
        "Player projection:",
        prop_table,
        "",
        data["closing"],
        "",
        BRAND_FOOTER,
    ]
    return "\n".join(parts)


def _build_player_chart_code(chart: dict[str, Any], filename: str) -> str:
    kind = chart.get("kind", "bar")
    x_values = chart["x_values"]
    y_values = chart["y_values"]
    line_value = chart.get("line_value")
    subject_label = chart.get("subject_label", "Recent games")
    x_label = chart.get("x_label", "Game")
    y_label = chart.get("y_label", "Value")

    body = _chart_preamble(chart["title"])
    body += f"\nx_values = {x_values!r}\n"
    body += f"y_values = {y_values!r}\n"

    if kind == "line":
        body += (
            "\nax.plot(x_values, y_values, marker='o', linewidth=2.4,"
            " color=BRAND_PRIMARY, label=" + repr(subject_label) + ")\n"
        )
    else:
        body += (
            "\nax.bar(x_values, y_values, color=BRAND_PRIMARY,"
            " edgecolor=BRAND_ACCENT, linewidth=0.6, label="
            + repr(subject_label)
            + ")\n"
        )

    if line_value is not None:
        body += (
            f"\nax.axhline({line_value!r}, color=BRAND_ACCENT, linestyle='--',"
            " linewidth=1.4, label='Projection line')\n"
        )

    body += (
        f"\nax.set_xlabel({x_label!r})\nax.set_ylabel({y_label!r})\n"
        "ax.legend(facecolor=BRAND_PANEL, edgecolor=BRAND_MUTED,"
        " labelcolor=BRAND_TEXT, loc='upper left')\n"
    )
    body += _chart_footer(filename)
    return body


# ---------------------------------------------------------------------------
# 1 PM -- Yesterday's Projection Review
# ---------------------------------------------------------------------------


def generate_review_post(data: dict[str, Any]) -> Post:
    """Generate the 1:00 PM review post.

    Expected ``data`` keys:
      date, review_date,
      mlb: list of {label, projection, actual, note (optional)},
      wnba: list of {label, projection, actual, note (optional)},
      deep_dives: list of {title, accurate (bool), narrative,
                           chart: {title, categories, projections, actuals}},
      adjustment_note (str)
    """
    text = _build_review_text(data)
    filename = f"review_{data['date']}.png"
    chart_code = _build_review_chart_code(data["deep_dives"][0]["chart"], filename)
    return Post(slot="1:00 PM ET", text=text, chart_filename=filename, chart_code=chart_code)


def _build_review_text(data: dict[str, Any]) -> str:
    mlb_rows = [
        [r["label"], _fmt_number(r["projection"], 2), _fmt_number(r["actual"], 2)]
        for r in data.get("mlb", [])
    ]
    wnba_rows = [
        [r["label"], _fmt_number(r["projection"], 2), _fmt_number(r["actual"], 2)]
        for r in data.get("wnba", [])
    ]

    sections = [
        DISCLAIMER,
        "",
        f"Projection Review — {data['review_date']}",
        "",
        "MLB — Projection vs Actual:",
        _markdown_table(["Item", "Projection", "Actual"], mlb_rows) if mlb_rows else "No MLB items.",
        "",
        "WNBA — Projection vs Actual:",
        _markdown_table(["Item", "Projection", "Actual"], wnba_rows) if wnba_rows else "No WNBA items.",
        "",
    ]

    for dive in data.get("deep_dives", [])[:2]:
        header = "Deep dive: " + dive["title"]
        if dive.get("accurate"):
            opener = "The engine accurately captured this outcome. "
        else:
            opener = "The engine missed on this one. "
        sections.append(header)
        sections.append(opener + dive["narrative"])
        sections.append("")

    sections.append("Forward-looking note:")
    sections.append(data["adjustment_note"])
    sections.append("")
    sections.append(BRAND_FOOTER)
    return "\n".join(sections)


def _build_review_chart_code(chart: dict[str, Any], filename: str) -> str:
    categories = chart["categories"]
    projections = chart["projections"]
    actuals = chart["actuals"]

    body = _chart_preamble(chart["title"])
    body += f"\ncategories = {categories!r}\n"
    body += f"projections = {projections!r}\n"
    body += f"actuals = {actuals!r}\n"
    body += """
import numpy as np
x = np.arange(len(categories))
width = 0.38
ax.bar(x - width/2, projections, width, label='Projection',
       color=BRAND_PRIMARY, edgecolor=BRAND_ACCENT, linewidth=0.6)
ax.bar(x + width/2, actuals, width, label='Actual',
       color=BRAND_ACCENT, edgecolor=BRAND_PRIMARY, linewidth=0.6)
ax.set_xticks(x)
ax.set_xticklabels(categories, rotation=15, ha='right')
ax.set_ylabel('Value')
ax.legend(facecolor=BRAND_PANEL, edgecolor=BRAND_MUTED,
          labelcolor=BRAND_TEXT, loc='upper left')
"""
    body += _chart_footer(filename)
    return body


# ---------------------------------------------------------------------------
# 4 PM -- WNBA Projection Post
# ---------------------------------------------------------------------------


def generate_wnba_projection_post(data: dict[str, Any]) -> Post:
    """Generate the 4:00 PM WNBA projection post.

    Same shape as MLB: data has hook, why, closing, game, prop, chart.
    Game keys: away, home, away_proj, home_proj, total_proj, market_total,
    tipoff_local, venue.
    """
    game = data["game"]
    prop = data["prop"]
    text = _build_wnba_text(data, game, prop)
    filename = f"wnba_projection_{data['date']}.png"
    chart_code = _build_player_chart_code(data["chart"], filename)
    return Post(slot="4:00 PM ET", text=text, chart_filename=filename, chart_code=chart_code)


def _build_wnba_text(data: dict[str, Any], game: dict[str, Any], prop: dict[str, Any]) -> str:
    why_lines = data.get("why", [])
    if not 3 <= len(why_lines) <= 5:
        raise ValueError("WNBA 'why' must contain 3-5 sentences.")

    matchup = f"{game['away']} @ {game['home']}"
    proj_table = _markdown_table(
        ["Metric", "Engine Projection", "Market"],
        [
            [f"{game['away']} points", _fmt_number(game["away_proj"], 2), "—"],
            [f"{game['home']} points", _fmt_number(game["home_proj"], 2), "—"],
            [
                "Total points",
                _fmt_number(game["total_proj"], 2),
                _fmt_number(game.get("market_total", 0.0), 1)
                if game.get("market_total") is not None
                else "—",
            ],
        ],
    )
    prop_table = _markdown_table(
        ["Player", "Market", "Line", "Engine Projection"],
        [[
            f"{prop['player']} ({prop['team']})",
            prop["market"],
            _fmt_number(prop["line"], 1),
            _fmt_number(prop["projection"], 2),
        ]],
    )

    parts = [
        DISCLAIMER,
        "",
        f"WNBA Projection — {matchup}",
        f"Tipoff: {game['tipoff_local']} | Venue: {game['venue']}",
        "",
        data["hook"],
        "",
        "Why the engine is here:",
        " ".join(why_lines),
        "",
        "Game projection:",
        proj_table,
        "",
        "Player projection:",
        prop_table,
        "",
        data["closing"],
        "",
        BRAND_FOOTER,
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Orchestrator + CLI
# ---------------------------------------------------------------------------


def generate_daily_posts(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Generate all three posts. Returns dict keyed by slot name."""
    return {
        "mlb_10am": generate_mlb_projection_post(payload["mlb_projection"]).to_dict(),
        "review_1pm": generate_review_post(payload["review"]).to_dict(),
        "wnba_4pm": generate_wnba_projection_post(payload["wnba_projection"]).to_dict(),
    }


def _write_outputs(posts: dict[str, dict[str, str]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, post in posts.items():
        (out_dir / f"{key}.txt").write_text(post["text"], encoding="utf-8")
        (out_dir / f"{key}_chart.py").write_text(post["chart_code"], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Edge Equation daily X posts.")
    parser.add_argument("--input", required=True, help="Path to daily JSON payload.")
    parser.add_argument(
        "--out",
        default="./out_posts",
        help="Output directory for post text + chart scripts.",
    )
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = generate_daily_posts(payload)
    _write_outputs(posts, Path(args.out))

    for key, post in posts.items():
        print(f"\n=== {key} ({post['slot']}) ===\n")
        print(post["text"])
        print(f"\n[chart -> {post['chart_filename']}]\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

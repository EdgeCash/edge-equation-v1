# Edge Equation Content Generator (`@TheEdgeEquation`)

Self-contained generator for the three scheduled daily X posts:

| Slot | Time (ET) | Post |
| --- | --- | --- |
| 1 | 10:00 AM | MLB projection (1 game + 1 player prop) |
| 2 | 1:00 PM | Yesterday's projection review (MLB + WNBA) |
| 3 | 4:00 PM | WNBA projection (1 game + 1 player prop) |

Brand rules baked in: every post leads with the exact disclaimer, every chart
carries `Powered by Edge Equation` in the footer, and tone is strictly
projection / analysis -- no betting, picks, advice, or hype language.

## Files

- `edge_equation_posts.py` — generator (3 functions + orchestrator + CLI).
- `example_input.json` — daily payload schema, fully populated.
- `README.md` — this document, including a full example day output.

## Run

From the repo root:

```bash
PYTHONPATH=src python -m edge_equation.content_gen.edge_equation_posts \
    --input src/edge_equation/content_gen/example_input.json \
    --out ./out_posts
```

This writes one `.txt` (post body, ready to paste into X) and one `.py`
(standalone Matplotlib chart script) per slot:

```
out_posts/
  mlb_10am.txt          mlb_10am_chart.py
  review_1pm.txt        review_1pm_chart.py
  wnba_4pm.txt          wnba_4pm_chart.py
```

Each chart script is fully self-contained -- run it directly to produce the
branded PNG (filename embedded in the script).

## Programmatic use

```python
from edge_equation.content_gen import generate_daily_posts
import json

payload = json.loads(open("today.json").read())
posts = generate_daily_posts(payload)
# posts["mlb_10am"]   -> {"slot", "text", "chart_filename", "chart_code"}
# posts["review_1pm"] -> ...
# posts["wnba_4pm"]   -> ...
```

## Daily input schema

The generator consumes a single JSON object with three top-level keys:
`mlb_projection`, `review`, and `wnba_projection`. See
[`example_input.json`](./example_input.json) for the full populated shape.

Key fields the engine must supply each day:

- **`mlb_projection`** / **`wnba_projection`**
  - `date`, `hook`, `closing` — short strings
  - `why` — list of 3–5 sentences (validated)
  - `game` — team names, projections, market total, venue/park, start time
  - `prop` — player, team, market, line, projection
  - `chart` — `kind` (`line` or `bar`), `title`, axes labels, x/y values,
    optional `line_value` overlay (the engine projection)

- **`review`**
  - `mlb`, `wnba` — lists of `{label, projection, actual}`
  - `deep_dives` — 1–2 entries; first one drives the bar chart
  - `adjustment_note` — forward-looking model note

## Scheduler integration (Buffer / Typefully)

The generator produces post text + a chart PNG per slot. Standard pipeline:

1. **Cron / GitHub Action** at ~9:00 AM ET runs the script with the day's
   engine payload and uploads the three text files + three PNGs to a folder
   (S3, Drive, etc.).
2. **Typefully**: use their HTTP API
   (`POST /v1/drafts/`) once per slot with the `.txt` body and the PNG
   attached; set `schedule-date` to 10:00, 13:00, 16:00 ET respectively.
3. **Buffer**: same shape via their `/1/updates/create.json` endpoint, with
   `media[picture]` pointing to the uploaded PNG and `scheduled_at` set per
   slot.
4. Both tools accept the markdown tables verbatim — X Premium renders them as
   plain monospaced text, which keeps the projection table tidy on mobile.

A minimal scheduler-side snippet (Typefully):

```python
import requests, pathlib
slots = {"mlb_10am": "10:00", "review_1pm": "13:00", "wnba_4pm": "16:00"}
for key, hhmm in slots.items():
    body = pathlib.Path(f"out_posts/{key}.txt").read_text()
    img  = pathlib.Path(f"out_posts/{key}_chart.png").read_bytes()  # rendered separately
    requests.post(
        "https://api.typefully.com/v1/drafts/",
        headers={"X-API-KEY": TYPEFULLY_KEY},
        json={"content": body, "schedule-date": f"{TODAY}T{hhmm}:00-04:00"},
    )
```

Render the PNGs in the same job, immediately after generating the `.py`
chart scripts:

```bash
for f in out_posts/*_chart.py; do python "$f"; done
```

---

## Example output — one full day

Below is the verbatim output of the generator using
[`example_input.json`](./example_input.json) (date: 2026-05-07).

### Slot 1 — 10:00 AM ET (MLB Projection)

```
Deterministic projections from our Edge Equation engine. For informational purposes only. Not advice or predictions of outcomes.

MLB Projection — Cleveland Guardians @ Seattle Mariners
First pitch: 9:40 PM ET | Park: T-Mobile Park

Engine highlight: a high-leverage pitching matchup with a sharp split between starter and bullpen projections.

Why the engine is here:
The home starter has posted a 28% CSW over his last five starts, well above his season baseline of 24%. The visiting lineup is sitting at a 27% strikeout rate over its last 14 games against right-handed pitching. Park factors at this venue suppress run scoring by roughly 6% versus league average in May conditions. Forecast wind is 7 mph in from center field, modestly compressing the home-run environment. The visiting bullpen has logged 14.1 innings over the previous three days, increasing reliance on lower-leverage arms in the middle frames.

Game projection:
| Metric | Engine Projection | Market |
| --- | --- | --- |
| Cleveland Guardians runs | 3.42 | — |
| Seattle Mariners runs | 3.86 | — |
| Total runs | 7.28 | 7.5 |

Player projection:
| Player | Market | Line | Engine Projection |
| --- | --- | --- | --- |
| Logan Gilbert (SEA) | Strikeouts | 6.5 | 7.42 |

Primary driver: starter swing-and-miss profile against a lineup running an elevated whiff rate vs RHP.

Powered by Edge Equation
```

Chart script (`mlb_10am_chart.py`) — produces `mlb_projection_2026-05-07.png`,
a bar chart of Logan Gilbert's strikeouts over the last 10 starts with the
engine projection (7.42) overlaid as a dashed reference line, all on the
brand dark-blue panel with the `Powered by Edge Equation` footer.

### Slot 2 — 1:00 PM ET (Review)

```
Deterministic projections from our Edge Equation engine. For informational purposes only. Not advice or predictions of outcomes.

Projection Review — 2026-05-06

MLB — Projection vs Actual:
| Item | Projection | Actual |
| --- | --- | --- |
| BAL @ NYY total runs | 8.14 | 7.00 |
| Corbin Burnes Ks | 6.91 | 8.00 |
| TEX team runs | 4.62 | 5.00 |

WNBA — Projection vs Actual:
| Item | Projection | Actual |
| --- | --- | --- |
| NYL @ CON total points | 158.40 | 161.00 |
| Sabrina Ionescu PRA | 36.20 | 31.00 |

Deep dive: Corbin Burnes strikeouts
The engine missed on this one. The engine over-weighted a two-start sample of reduced velocity from April; the most recent bullpen session indicated a return to baseline that the model had not yet ingested. A faster decay weight on velocity-derived inputs is being added for the next refresh.

Deep dive: Sabrina Ionescu PRA
The engine missed on this one. The engine gave heavy weight to recent usage trends; the matchup-side defensive switch onto the primary creator reduced touches in the second half. Defensive matchup tagging will be promoted from a secondary to a primary feature for guard PRA projections.

Forward-looking note:
Two adjustments are queued for tonight's run: faster velocity-decay weighting for starter K projections, and elevated weight on opponent defensive matchup tags for WNBA guard PRA.

Powered by Edge Equation
```

Chart script (`review_1pm_chart.py`) — grouped bar chart, Projection vs
Actual, for the deep-dive subject (Burnes' last 5 starts in the example).

### Slot 3 — 4:00 PM ET (WNBA Projection)

```
Deterministic projections from our Edge Equation engine. For informational purposes only. Not advice or predictions of outcomes.

WNBA Projection — New York Liberty @ Connecticut Sun
Tipoff: 7:00 PM ET | Venue: Mohegan Sun Arena

Engine highlight: a pace-up matchup with a clear usage concentration on the away side.

Why the engine is here:
The away team is averaging 84.2 possessions per 40 minutes over its last six games, the highest in the league. The home defense is allowing 1.06 points per possession in the half-court over the last 10 games. Primary creator usage has trended up to 31.4% over the last four games following a backcourt rotation change. Rest advantage favors the away side by one full day off; the home side is on the back end of a back-to-back.

Game projection:
| Metric | Engine Projection | Market |
| --- | --- | --- |
| New York Liberty points | 84.60 | — |
| Connecticut Sun points | 79.10 | — |
| Total points | 163.70 | 161.5 |

Player projection:
| Player | Market | Line | Engine Projection |
| --- | --- | --- | --- |
| Sabrina Ionescu (NYL) | Points + Rebounds + Assists | 33.5 | 35.80 |

Primary driver: pace and usage concentration on a rest-advantaged back-to-back spot for the home defense.

Powered by Edge Equation
```

Chart script (`wnba_4pm_chart.py`) — line chart of Ionescu's PRA over her
last 10 games with the engine projection (35.8) as a dashed teal reference
line, brand footer present.

---

## Brand guardrails enforced in code

- Disclaimer prepended to every post body verbatim (single source: `DISCLAIMER`).
- `Powered by Edge Equation` rendered into every chart via the shared chart
  footer helper (single source: `BRAND_FOOTER`).
- Chart palette fixed: deep blue background, teal primary, light cyan accent,
  white text — set once in `_chart_preamble`.
- The `why` block validates that the engine supplied 3–5 sentences.
- No emoji, no hype words, no win/loss language anywhere in the templates.

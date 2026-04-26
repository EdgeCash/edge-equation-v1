# PrizePicks prop tracking on iPhone — Numbers + GitHub Actions fetcher

A no-server-on-your-phone workflow for tracking PrizePicks props by
hand. Everything you do is in **Numbers**; a server-side workflow keeps
fresh projection data in a CSV your iPhone can download in one tap.

- **Numbers** holds the workbook (raw projections, your math, results
  tracking). Touch-optimized UI, free, syncs over iCloud.
- **GitHub Actions** runs `tools/sheets/fetch_prizepicks.py` every hour
  (10am–11pm CT) and commits the latest projections to
  `data/prizepicks/latest.csv`. History accumulates in
  `data/prizepicks/snapshots/` for player-accuracy analysis.
- **Safari bookmark** + **Open in Numbers** is the bridge: tap the
  bookmark → CSV downloads → paste into the Numbers `_raw` sheet.
  Formulas in `picks` and `tracking` update instantly.

> Earlier drafts of this runbook used an iOS Shortcut to fetch
> directly on the phone. The Shortcuts dictionary/loop wiring kept
> regressing, and the architecture was fundamentally fragile —
> server-side Python is the right tool here. That section has been
> retired.

---

## One-time setup

### 1. Download the prebuilt workbook

The sheets, columns, formulas, and the `payout_mult` named cell are
already wired up. You just download once.

1. On iPhone, open **Safari**.
2. Go to:
   ```
   https://github.com/EdgeCash/edge-equation-v1/raw/main/tools/sheets/edge_equation_props.xlsx
   ```
3. Safari downloads the file. Tap the downloads icon → tap
   `edge_equation_props.xlsx`.
4. iOS shows a preview. Tap the share icon (square + up arrow, top-right).
5. Tap **Open in Numbers**. Numbers asks if you want to convert. Tap
   **Convert**. The workbook opens with `_raw`, `picks`, and `tracking`
   sheets and all formulas live.

### 2. Bookmark the latest-projections CSV

The GitHub Actions workflow commits a fresh CSV every hour while
PrizePicks is active. The download URL is stable:

```
https://github.com/EdgeCash/edge-equation-v1/raw/main/data/prizepicks/latest.csv
```

1. Open that URL in Safari once.
2. Tap the share icon → **Add Bookmark** (or **Add to Home Screen** for
   one-tap access).
3. Name it something like "PrizePicks fresh."

That's the entire setup. From here on, daily use is two taps + a paste.

---

## Daily flow

### When you want fresh projections

1. Tap the **PrizePicks fresh** bookmark.
2. Safari downloads `latest.csv`. Tap the file in the downloads list.
3. iOS preview appears. Tap the share icon → **Copy to Numbers**, OR
   **Open in Numbers** to load it as a new spreadsheet so you can copy
   the rows into your workbook.
4. With the rows copied, switch to your `Edge Equation — Props`
   workbook → `_raw` sheet → tap cell **A2** → paste. The 12 columns
   land in row 2 onward.

### When you want to handicap a prop

1. Skim `_raw` for a projection that interests you. Note its
   `projection_id` (column B).
2. Switch to the `picks` sheet → paste the projection_id into column A
   of an empty row.
3. Player / team / league / stat / line auto-fill via VLOOKUP.
4. Type your `pick` (over / under) in column G.
5. Type your fair-probability estimate in column H (e.g., `0.58` for
   58%). The sheet computes break_even, edge, half-Kelly, and grade
   automatically.

### After the game

1. Switch to `tracking` → paste the same projection_id into column A.
2. Fill in column F (`actual` — the real stat the player put up) and
   column H (`units_risked`, e.g. `1` for one unit).
3. Hit / push / loss + units P&L compute themselves.

---

## Bumping the payout multiplier

PrizePicks pays differently by entry size:

| Entry        | Multiplier |
|--------------|-----------:|
| 2-pick power | 3          |
| 3-pick power | 5          |
| 4-pick power | 10         |
| 5-pick power | 20         |
| 6-pick power | 25         |

The workbook defaults to **3** (2-pick). Change cell **Q1** of the
`picks` sheet to switch — every break-even / Kelly / P&L formula
re-computes.

---

## Player-accuracy analysis (later)

Once snapshots accumulate in `data/prizepicks/snapshots/`, you can:

1. Pull the snapshot files locally (or via GitHub's web UI).
2. Group by `player`, count rows where you tracked a `pick` decision
   and the game settled.
3. Compute hit rate per player. Players who hit > expected rate are
   worth deeper looks; those below are worth fading.

This is exactly the calibration loop the `reliability` CLI does for
the engine's MLB picks (`python -m edge_equation reliability --sport
MLB --market ML`). For props, the same analysis runs against your
manual picks once you have ≥30 settled rows per player.

---

## Manual override (no workflow needed)

If GitHub Actions is paused or you want to test ad-hoc, you can run
the fetcher locally:

```bash
python3 tools/sheets/fetch_prizepicks.py
# default writes data/prizepicks/latest.csv

python3 tools/sheets/fetch_prizepicks.py --league MLB --max-pages 1
# MLB only, single page (~250 projections)
```

The script has no dependencies beyond Python 3.10+ stdlib.

---

## Troubleshooting

- **Bookmark downloads an empty CSV.** The CT-hour guard in the
  workflow is paused (overnight) or PrizePicks returned no
  projections. Wait until ~10am CT or trigger the workflow manually
  via Actions → PrizePicks Fetcher → **Run workflow**.
- **403 Forbidden in workflow logs.** PrizePicks tightened their UA
  filter. Edit `tools/sheets/fetch_prizepicks.py` → `DEFAULT_UA` to a
  more current iPhone Safari string.
- **Numbers VLOOKUP shows blank for player/team/league.** The CSV
  might have landed without those columns populated — check the
  workflow log for `players_seen` / `leagues_seen` in the summary
  line. If those are 0, PrizePicks changed their `included` payload
  shape; the script needs to follow the new keys.
- **Snapshots directory growing too large.** It's append-only by
  design (history for analysis). If you need to trim, delete old
  files in `data/prizepicks/snapshots/` and commit; the workflow
  starts fresh on the next run.

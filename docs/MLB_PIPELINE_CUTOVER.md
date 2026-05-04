# MLB Pipeline Cutover — Phase 4

This document is the runbook for taking the `integrate-scrapers-content-v1`
branch from "merged but flagged off" to "live, scrapers idled."

## Pre-flight

- [ ] Branch `integrate-scrapers-content-v1` merged into `main` via PR.
- [ ] `vars.MLB_PIPELINE_ENABLED` (repo variable, NOT secret) is `false`.
- [ ] Repo secret `ODDS_API_KEY` is set.
- [ ] Stub modules replaced with verbatim scrapers source — run:
      ```bash
      ./scripts/sync_scrapers_modules.sh   # see "Stub-replacement script"
      ```
- [ ] Pulled goldmine history into `data/scrapers_history/mlb/`:
      ```bash
      mkdir -p data/scrapers_history/mlb
      for f in picks_log.json backtest.json calibration.json clv_summary.json; do
        curl -fsSL "https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/public/data/mlb/$f" \
          -o "data/scrapers_history/mlb/$f"
      done
      ```

## Side-by-side test (5 days minimum)

Each morning before scrapers' cron fires (11 AM ET), run v1's pipeline
manually with the feature flag on:

```bash
EDGE_FEATURE_SPREADSHEET_PIPELINE=on \
  python -m edge_equation.exporters.mlb.daily_spreadsheet \
  --include-backfill \
  --output-dir reports/parallel/$(date -u +%Y-%m-%d)
```

After scrapers' run completes, diff the two outputs:

```bash
python -m edge_equation.backtest.cli diff \
  reports/parallel/$(date -u +%Y-%m-%d)/backtest.json \
  data/scrapers_history/mlb/backtest.json
```

**Pass criteria:** v1's per-market `roi_pct` is within ±1% of scrapers
on every gated market AND v1's Brier is ≤ scrapers' Brier on at least 4
of 5 days. If totals/F5 Brier comes in materially LOWER (better) on v1,
that confirms the NegBin port fixed the regression.

## Cutover

1. **Stop the scrapers cron.** In `EdgeCash/edge-equation-scrapers`,
   open `.github/workflows/mlb-daily.yml` and `mlb-closing-lines.yml`
   and comment out the `schedule:` blocks. Push to scrapers' main.
   Keep `workflow_dispatch:` so manual emergency runs still work.

2. **Enable v1's cron.** In `EdgeCash/edge-equation-v1` repo settings →
   Variables, set `MLB_PIPELINE_ENABLED=true`. The two new workflows'
   `if:` clauses gate on this variable; flipping it from `false` to
   `true` is the actual cutover moment.

3. **Watch the first cron run** (15:00 UTC = 11 AM ET DST). Confirm:
   - Workflow succeeds (Actions tab → MLB Daily Spreadsheet).
   - A new commit lands on `main` with title `Daily MLB spreadsheet — YYYY-MM-DD`.
   - Files in `public/data/mlb/` updated.
   - Vercel auto-deploy triggered (Vercel dashboard).
   - `mlb_daily.xlsx` opens, all 8 tabs present, grade + kelly_advice
     formulas show correct values in row 4+.

4. **Watch the first closing-line snapshot** (`*/30 17-23 * * *`).
   `picks_log.json` should grow `closing_price_*` fields on settled rows.

## Rollback

If anything goes wrong in steps 3 or 4:

1. Set `MLB_PIPELINE_ENABLED=false` in v1 repo variables. Cron stops
   immediately.
2. Re-enable scrapers cron by uncommenting its `schedule:` blocks and
   pushing.
3. If a bad commit landed in `public/data/mlb/`, revert it:
   ```bash
   git revert <bad-sha>
   git push origin main
   ```
   Vercel will redeploy the previous good snapshot.

The feature flag means rollback is one variable flip — no code revert
required for the model itself.

## Post-cutover monitoring (first 14 days)

Daily, after the morning workflow run:

```bash
# Sanity: were any picks shipped today?
jq '.todays_card | length' public/data/mlb/mlb_daily.json

# Per-market gate decisions
jq '.gate_notes' public/data/mlb/mlb_daily.json

# Are gated markets clearing the rolling thresholds?
python -c "
import json
bt = json.load(open('public/data/mlb/backtest.json'))
for r in bt['summary_by_bet_type']:
    flag = '✓' if (r['bets']>=200 and r['roi_pct']>=1.0 and (r['brier'] or 1)<0.246) else '✗'
    print(f\"{flag} {r['bet_type']:<14} bets={r['bets']:<5} roi={r['roi_pct']:>+6.2f}% brier={r['brier']}\")
"
```

Watchlist:
- **Pick volume.** If a market that was gated-out for weeks suddenly
  passes and ships >5 picks/day, hold a manual review before trusting it.
- **CLV slipping.** `clv_summary.json` should show positive CLV on a
  rolling 30-day basis. Sustained negative CLV is the earliest signal
  of model drift.
- **Workflow runtime.** Both workflows should stay under 5 min. If
  runtime creeps past 10 min, the backtest replay window has grown too
  large — cap to last 4 seasons in the next iteration.

## Idling scrapers permanently

After 30 days of clean v1 operation, archive the scrapers repo on GitHub
(Settings → Archive). The historical `public/data/mlb/` data stays
readable as a static archive; v1's `data/scrapers_history/mlb/` already
holds a snapshot of the goldmine for any future replay needs.

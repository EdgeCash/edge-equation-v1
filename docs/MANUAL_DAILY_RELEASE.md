# Manual Daily Release SOP

**Status:** active since 2026-05-01.

The public-testing release runs in **manual-trigger mode**. Daily
crons are disabled across all user-facing workflows so the operator
can wait for confirmed lineups, weather, and umpires before sending
the daily email and publishing to the website.

This document is the canonical step-by-step workflow.

## Why manual

During public testing, **quality > speed**. A bad day shipped
automatically damages credibility we're spending three months building.
A good day shipped two hours later costs nothing. Manual gating is
the right trade.

We'll flip back to automatic crons when:
- The engine has 60+ days of public track record on the website
- We're entering the paid-tier launch window for football season

To re-enable: search for `MANUAL TRIGGER MODE` in
`.github/workflows/*.yml`, uncomment the `schedule:` block in each
file. That's it; nothing else is gated on the schedule.

## What's disabled

These daily crons are commented out (not deleted — restoration is a
one-line uncomment per file):

| Workflow | What it does | When operator triggers |
|---|---|---|
| `nrfi-daily-email.yml` | Sends NRFI/YRFI email + publishes today's picks to `/daily-edge` | Daily, after lineups confirmed |
| `website-publish-track-record.yml` | Publishes settled-pick history to `/track-record` | Daily, after the email step |
| `daily-edge.yml` | Premium daily-edge content | Pause during testing |
| `evening-edge.yml` | Evening edge content | Pause during testing |
| `overseas-edge.yml` | Overseas content | Pause during testing |
| `premium-daily-preview.yml` | Premium daily preview | Pause during testing |
| `spotlight.yml` | Spotlight content | Pause during testing |
| `that-k-report.yml` | K Report supporting workflow | Pause during testing |
| `ledger.yml` | Ledger update | On demand |

These data-plumbing crons keep running on schedule (they don't
publish anything publicly):

- `data-refresher.yml` — keeps source data fresh
- `results-settler.yml` — settles game outcomes nightly
- `prizepicks-fetch.yml` — hourly PrizePicks fetch
- `nrfi-daily-odds-snapshot.yml` — odds capture
- `nrfi-weekly-retrain.yml` — weekly Monday retrain

## The Daily Release Workflow

Open the GitHub mobile app or web → repo → **Actions** tab.

### Step 1 — Check first-pitch time for the day

Use any sports app, MLB.com, or ESPN to find the slate's earliest
first pitch. Mark a target window: **lineups + weather should be
posted at least 90 minutes before first pitch.**

If the first game is at 12:35 CT, your window opens around 11:00 CT
(weather + lineups). Don't trigger before that.

### Step 2 — Run the engine in preview mode

Action: **NRFI/YRFI Daily Email** → "Run workflow"

Inputs:
- `confirm_lineups_posted`: **`false`** (we're previewing)
- `target_date`: leave blank (= today)
- `dry_run`: **`true`** (build the card and log it, do not send email)

The dry-run will build the email + write the card output to the
job log so you can review the picks without sending. The lineup
gate is bypassed when `dry_run=true` is paired with
`confirm_lineups_posted=false` only because the gate's purpose is
to prevent accidental real sends — but if you set
`confirm_lineups_posted=false` AND `dry_run=false`, the workflow
refuses to run.

> **Note:** the current implementation gates ALL `workflow_dispatch`
> runs on `confirm_lineups_posted=true`. If you want a true preview
> run, set both `confirm_lineups_posted=true` AND `dry_run=true`.
> The lineup-confirmation gate is the OPERATOR'S explicit attestation
> that lineups are posted; dry_run is the SAFETY against accidental
> email sends. Both serve different purposes.

Review the picks in the workflow log. If they look honest:

- Tier distribution looks reasonable for the slate (not all NO_PLAY,
  not unrealistically skewed toward STRONG/ELITE)
- Notes mention the right pitchers, weather conditions, lineups
- No `(unknown)` or `(missing)` strings where data should be

### Step 3 — Send for real

Action: **NRFI/YRFI Daily Email** → "Run workflow"

Inputs:
- `confirm_lineups_posted`: **`true`** (REQUIRED — workflow refuses without)
- `target_date`: leave blank
- `dry_run`: **`false`**

This:
1. Hydrates DuckDB from the latest backfill artifact
2. Settles yesterday's NRFI picks into `nrfi_pick_settled` (so they
   show up on the public ledger today)
3. Builds today's email card from `run_daily.py`
4. Sends the email to the configured recipient
5. Runs `build_daily_feed` to write today's picks to
   `website/public/data/daily/latest.json`
6. Commits that JSON back to `main` — Vercel auto-deploys

After ~3-4 minutes the email arrives and `/daily-edge` shows
today's picks.

### Step 4 — Publish the track record

Action: **Website · Publish Track Record** → "Run workflow"

No inputs needed. This:
1. Pulls the freshly-uploaded DuckDB from the daily-email run you
   just completed
2. Walks `nrfi_pick_settled` (now containing yesterday's settled
   picks), filters to LEAN-and-above, normalizes
3. Writes `ledger.json` / `summary.json` / `by-day.json` to
   `website/public/data/track-record/`
4. Commits back to `main` — Vercel auto-deploys

After ~2-3 minutes `/track-record` shows the updated history.

### Step 5 — Verify on the live site

Open the deployed site and check:

- **`/daily-edge`** — today's picks rendered with the right tier
  colors, conviction percentages, and game labels. The "generated_at"
  timestamp at the bottom should be within the last few minutes.
- **`/track-record`** — yesterday's settled picks now appear in the
  ledger; the running-record cards reflect the new W/L/Push counts.

If anything looks wrong, **don't post anywhere yet.** Investigate
first — once a link is shared on X it's harder to walk back.

### Step 6 (optional) — Light X post

If everything looks right, post lightly on X. The track record is
the asset; let it speak. Examples:

- "Today's NRFI board is up: edgeequation.com/daily-edge"
- "Track record updated: edgeequation.com/track-record · {n} picks
  logged · {hit_pct}% on STRONG tier"

Don't over-post. One link per day is enough.

## Safety checks built in

| Check | What it prevents |
|---|---|
| `confirm_lineups_posted` gate on email workflow | Accidentally sending before MLB confirmed lineups |
| `dry_run` mode | Reviewing the card without sending email |
| Track-record exporter never mutates DuckDB | A failed export can't corrupt training data |
| `latest.json` commit only happens after email send succeeds | Website never shows picks the email subscribers haven't seen |
| Idempotent commits — `git diff --cached --quiet` short-circuits | Re-running the workflow on a slate with no changes is a no-op |

## What NOT to do

- ❌ Don't trigger the daily email before lineups post (defeats the
  point of manual mode).
- ❌ Don't trigger the track-record workflow BEFORE the daily-email
  workflow on a fresh day. The track-record workflow reads from the
  daily-email artifact; running them in the wrong order publishes
  stale data.
- ❌ Don't push manual edits to `website/public/data/daily/latest.json`
  or `website/public/data/track-record/*.json`. They're generated.
  Hand-edits get clobbered by the next workflow run.
- ❌ Don't re-enable the crons piecemeal. Either stay fully manual
  or flip everything together when leaving testing mode.

## Troubleshooting

**"Lineup confirmation NOT given"** — you forgot to set
`confirm_lineups_posted=true` in the Run-workflow form. Re-trigger
with the box checked.

**Track Record shows stale picks** — you ran the track-record
workflow before the daily-email workflow today. Run daily-email
first, then track-record.

**`/daily-edge` shows yesterday's picks** — check whether the
"Commit + push daily feed if changed" step succeeded in the
daily-email workflow. The commit may have been blocked by branch
protection; check the run log.

**`No JSON written — DuckDB hydration probably failed.`** — none
of the source workflows have a recent `nrfi-duckdb-latest` artifact.
Trigger a `nrfi-weather-backfill` or fresh daily-email run first.

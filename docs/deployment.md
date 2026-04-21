# Deployment guide

Two ways to run Edge Equation autonomously.  Pick one — both end in the same place: `python -m edge_equation daily --publish --no-dry-run` firing on a schedule with real credentials.

## Option 1 — GitHub Actions (recommended)

Free for public repos. No extra infrastructure beyond the repo itself. Already wired up in `.github/workflows/`.

### Workflows shipped

| file | what it does | trigger |
| --- | --- | --- |
| `tests.yml` | Runs `pytest` on every push to main and every PR. | push / pull_request |
| `daily-edge.yml` | Runs `python -m edge_equation daily --publish --no-dry-run` | cron `0 13 * * *` UTC (9am ET) + manual |
| `evening-edge.yml` | Runs `python -m edge_equation evening --publish --no-dry-run` | cron `0 22 * * *` UTC (6pm ET) + manual |
| `settle.yml` | Records outcomes from a CSV and settles stored picks. | manual (workflow_dispatch) |

### Secrets to set

Repo → Settings → Secrets and variables → Actions → New repository secret.

Add any of the following that apply. Missing secrets are fine — each publisher falls through to its file failsafe, which is uploaded as a workflow artifact you can download from the run page.

- `THE_ODDS_API_KEY`
- `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`
- `DISCORD_WEBHOOK_URL`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `EMAIL_TO`, `SMTP_TO`

### Manual trigger

Actions tab → Daily Edge / Evening Edge → Run workflow. Override `leagues`, `publish`, or `dry_run` via the form.

### State persistence

GitHub Actions runners are ephemeral, but each workflow caches `edge_equation.db` under `actions/cache@v4` keyed on the branch. This preserves `slate_id` idempotency and realization history across runs. Cache entries are evicted after 7 days of inactivity — for a durable store, migrate to a hosted SQLite (Turso, Cloudflare D1) or Postgres in a later phase.

Failsafe artifacts are uploaded after every run and retained for 14 days.

## Option 2 — Vercel Cron

Vercel supports scheduled serverless invocations via the `vercel.json` `crons` field. The API layer (`api/main.py`) already exposes `/cron/daily` and `/cron/evening` endpoints that invoke the same `ScheduledRunner.run()` behind bearer auth.

### Setup

The existing Vercel project in this repo is rooted at `/website` (Next.js UI), which means a `vercel.json` at the repo root is ignored by that project. To use Vercel Cron you need a **second** Vercel project configured with its root directory at `/`:

1. Vercel Dashboard → Add New → Project → Import the same repo.
2. In Configure Project: **Root Directory** = `.` (the repo root, not `website`).
3. Framework Preset: Other. Build Command: leave blank. Output Directory: blank.
4. Copy `deployment/vercel-api.json` to the repo root as `vercel.json` **on a branch used only for this API project** (or toggle "Ignored Build Step" on the UI project so it doesn't fire on that branch).
5. Set environment variables on the API project:
   - `CRON_SECRET` — any strong random string. Vercel will inject this as `Authorization: Bearer $CRON_SECRET` on cron invocations.
   - All the other secrets from the `.env.example` file that apply.

Vercel will deploy `api/main.py` as a Python serverless function. Crons fire at the schedules in `vercel.json`.

### Why not one Vercel project?

Because the API is Python and the website is Next.js, they need different root directories and different runtimes. You can do monorepo magic with Vercel, but splitting into two projects is the cleaner path and keeps this repo's current commit history (which removed a root `vercel.json` for exactly this reason) intact.

## Option 3 — anywhere else

Any scheduler that can run `python -m edge_equation daily --publish --no-dry-run` works. Examples that exist in the wild:

- **systemd timer** on a small VM.
- **cron** on a workstation that's always on.
- **Railway / Fly.io / Render cron** with a Python runtime.

The CLI prints JSON on stdout; the exit code is 0 unless a publisher hard-failed (publisher failures that triggered a failsafe still exit 0 because the failure was captured).

## Local smoke test

```bash
pip install -e ".[dev]"
cp .env.example .env
# edit .env — THE_ODDS_API_KEY is enough to exercise MLB ingestion
source .env && export $(grep -v '^#' .env | xargs)

python -m edge_equation daily              # dry-run, no publish
python -m edge_equation daily --publish    # dry-run, publishers fanout
python -m edge_equation daily --publish --no-dry-run   # GO LIVE
```

Expected exit code: 0. Expected stdout: a JSON `RunSummary`.

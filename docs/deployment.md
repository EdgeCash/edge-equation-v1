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

GitHub Actions runners are ephemeral. Two storage modes are supported:

1. **Cached SQLite file (default)** — `actions/cache@v4` holds `edge_equation.db` keyed on the branch. Preserves `slate_id` idempotency and realization history across runs. Entries evict after 7 days of inactivity.
2. **Hosted Turso (recommended for durable state)** — see [Hosted database](#hosted-database-turso) below. Set `EDGE_EQUATION_DB` to a `libsql://` URL and `TURSO_AUTH_TOKEN` to a valid bearer. The cache step becomes a no-op; all state lives in Turso.

Failsafe artifacts are uploaded after every run and retained for 14 days.

## Hosted database (Turso)

The persistence layer dispatches on the URL scheme of `EDGE_EQUATION_DB`:

| `EDGE_EQUATION_DB` | Backend |
| --- | --- |
| Plain path (e.g. `./edge_equation.db`, `/var/lib/edge/edge.db`) | stdlib `sqlite3` |
| `file://...` | stdlib `sqlite3` |
| `libsql://...`, `wss://...`, `https://...`, `http://...` | Turso HTTP pipeline adapter |

### Setup

1. Create a free Turso account and a database (≤9 GB, 1 B reads / 25 M writes / month).
2. `turso db show <name>` gives the URL (e.g. `libsql://edge-equation-prod-yourorg.turso.io`).
3. `turso db tokens create <name>` gives a bearer token.
4. In your deploy environment (GitHub Actions secrets, Vercel, local `.env`):
   ```
   EDGE_EQUATION_DB=libsql://edge-equation-prod-yourorg.turso.io
   TURSO_AUTH_TOKEN=<paste>
   ```
5. Run `python -m edge_equation daily` once. `Database.migrate()` creates every table in Turso automatically — the same `MIGRATIONS` tuple runs verbatim.

### How the adapter works

- `Database.open(url)` returns a `TursoConnection` that implements just enough of `sqlite3.Connection` for the rest of the codebase (`execute`, `executescript`, `cursor`, `commit`, `close`, `row_factory`).
- Each `conn.execute()` is one POST to `{url}/v2/pipeline` via `httpx` (already a project dependency). Turso autocommits each request.
- Stores (`PickStore`, `SlateStore`, `OddsCache`, `RealizationStore`, `GameResultsStore`) are unchanged. The `tests/test_turso_integration.py` suite proves every round-trip works against a SQLite-backed emulator that speaks the Turso wire protocol.

### Limitations (documented, non-blocking for current usage)

- **No multi-statement transactions.** `conn.commit()` is a no-op — every pipeline execute commits on its own. All existing stores commit immediately after their single INSERT/UPDATE, so this is fine.
- **Positional params only.** Named params (`:x`) raise `NotImplementedError` — none of the stores use them.
- **Typed args round-trip as strings.** Integer, float, text, blob, and null are supported. Decimals are stored as text (as they are on SQLite) so precision is preserved.

### Migration from existing SQLite

```bash
# Dump your local database
sqlite3 edge_equation.db .dump > edge.sql

# Apply to Turso
turso db shell <db-name> < edge.sql
```

Then flip `EDGE_EQUATION_DB` to the Turso URL and redeploy. No code changes required.

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

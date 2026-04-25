# External scheduler setup

## Why this exists

GitHub Actions' built-in cron scheduler is documented as "best effort" and
regularly delays scheduled workflows by 2–9 hours under load. We observed
this firsthand on 2026-04-24 — the Premium Daily email that's scheduled
for 10 AM CT fired at 12:56 PM CT because GitHub's cron queue was
saturated.

A 4-hour tolerance guard papered over the common case but couldn't save
the worst days. For beta testing where picks depend on landing before
games start, we need **actual** 10 AM CT execution, not "somewhere
between 10 AM and 1 PM CT, probably."

This runbook wires an external cron service (cron-job.org) to fire
GitHub's `workflow_dispatch` API at precise times. GitHub runs
dispatched workflows within seconds of the trigger, with no queue
delay. The GitHub cron schedules stay in place as a fallback in case
the external scheduler ever goes down.

**Architecture:**

    cron-job.org (real cron, fires on time)
         │
         ▼  HTTPS POST with GitHub fine-grained PAT
    GitHub REST API /actions/workflows/<file>/dispatches
         │
         ▼  fires within seconds
    GitHub Actions workflow runs
         │
         ▼
    Engine runs, writes picks to Turso, emails Premium Daily

## Why we're keeping GitHub Actions as the compute

The engine is 5000+ lines of Python with 1500+ tests. Migrating off
GitHub Actions means either rewriting it in JavaScript (months of work,
high regression risk) or spinning up new infrastructure (VPS, Railway,
Fly.io). The cron problem is separable from the compute problem — fix
scheduling without touching what runs.

---

## Step 1 — Create a GitHub fine-grained PAT (5 min)

Permissions: minimum required to dispatch workflows on this repo.

1. GitHub → click your avatar → **Settings**
2. Left sidebar → **Developer settings**
3. **Personal access tokens** → **Fine-grained tokens**
4. **Generate new token**
5. Fill in:
   - **Token name**: `edge-equation external scheduler`
   - **Expiration**: 1 year (calendar it to rotate before it expires)
   - **Description**: `External cron dispatches workflow runs via REST API`
   - **Resource owner**: `EdgeCash`
   - **Repository access**: **Only select repositories** → choose
     `edge-equation-v1`
6. **Repository permissions**:
   - **Actions**: **Read and write**
   - (GitHub will auto-add Metadata: Read — leave it)
7. Leave everything else untouched
8. **Generate token**
9. **COPY THE TOKEN NOW** — it's only shown once. Save it somewhere safe
   (password manager) — you'll paste it into cron-job.org in Step 3.

## Step 2 — Create a cron-job.org account (2 min)

1. Go to **cron-job.org**
2. Sign up (free tier — allows 50 cron jobs, 1-minute precision, is more
   than enough)
3. Verify your email

## Step 3 — Create one cron job per cadence (5 min)

For each workflow below, add one cron job in cron-job.org:

| Priority | Workflow file                | When (America/Chicago) |
|----------|------------------------------|------------------------|
| 1        | `data-refresher.yml`         | 07:00                  |
| 2        | `data-refresher.yml`         | 15:00 (second refresh) |
| 3        | `premium-daily-preview.yml`  | 10:00                  |
| 4        | `ledger.yml`                 | 09:00                  |
| 5        | `daily-edge.yml`             | 11:00                  |
| 6        | `spotlight.yml`              | 16:00                  |
| 7        | `evening-edge.yml`           | 18:00                  |
| 8        | `overseas-edge.yml`          | 23:00                  |
| 9        | `results-settler.yml`        | 02:00 (next day)       |
| 10       | `that-k-report.yml`          | 09:00 (if you use it)  |

Setup priorities 1-3 first if you're short on time — those are the
must-haves for daily operation. Add the others later.

For each job in cron-job.org:

1. Dashboard → **Create cronjob**
2. Title: **EdgeCash — \<workflow name\>** (e.g. `EdgeCash — Premium Daily`)
3. **URL**:
       https://api.github.com/repos/edgecash/edge-equation-v1/actions/workflows/<WORKFLOW_FILE>/dispatches

   Replace `<WORKFLOW_FILE>` with the file name from the table above
   (e.g. `premium-daily-preview.yml`).

4. **Schedule**: click **Expert mode** (or "custom")
   - **Timezone**: `America/Chicago` (not UTC — this handles DST for you
     automatically, which is why you don't need dual cron entries)
   - **Minute**: `0`
   - **Hour**: value from the table (e.g. `10` for Premium Daily)
   - **Day**: `every`
   - **Month**: `every`
   - **Weekday**: `every`

5. Expand **Advanced** → **Request method**: `POST`

6. **Headers** — add these three:

       Authorization: Bearer <YOUR_PAT>
       Accept: application/vnd.github+json
       Content-Type: application/json

   Replace `<YOUR_PAT>` with the token from Step 1.

7. **Request body**:

       {"ref": "main"}

   This tells GitHub to run the workflow from the `main` branch.

8. **Save** and make sure the job is **enabled**.

## Step 4 — Verify one job fires end-to-end (2 min)

1. On cron-job.org, find your **Premium Daily** job and click **Run now**
2. In a new tab, go to GitHub → Actions → **Premium Daily (10am CT)**
3. You should see a new run started **within 5–10 seconds**, with the
   event type **workflow_dispatch** (not "scheduled")
4. Wait for the green check

If GitHub shows a new run immediately, you're done. External scheduling
is live.

If not, see Troubleshooting below.

## Step 5 — Decide whether to kill the GitHub cron schedule

**Recommended: keep both.** GitHub's `on: schedule:` stays in place as a
fallback. If cron-job.org is down for maintenance or your PAT expires,
the GitHub schedule still fires (late, but it fires). Idempotency (slate
already built) prevents double-emails when both fire the same day.

If you ever want to remove the GitHub schedule, you'd delete the
`schedule:` block from each workflow file. Keep it for now.

---

## Troubleshooting

**cron-job.org job fires but GitHub shows no new run**

- Check the cron-job.org execution log (last-response section) — the
  raw HTTP response from GitHub is there.
- `404 Not Found` → wrong URL. Check the workflow file name (case
  sensitive, including the `.yml` extension). Verify your repo slug
  is `edgecash/edge-equation-v1`.
- `401 Unauthorized` → bad PAT. Most likely either mistyped, or the
  PAT's repository scope doesn't include this repo, or
  Actions permission isn't set to Read + write.
- `422 Unprocessable Entity` → request body format issue. Confirm
  body is exactly `{"ref": "main"}` and Content-Type is
  `application/json`.

**GitHub shows the run started but the workflow immediately skips**

- The existing guard in each cadence has a short-circuit for
  `workflow_dispatch` events — it auto-sets `should_run=true`. If the
  workflow skips anyway, the issue is elsewhere in the workflow logic,
  not the dispatch mechanism.

**All jobs failed suddenly**

- The most common cause is PAT expiration. Fine-grained PATs expire.
  Go back to GitHub → Settings → Developer settings and generate a new
  one. Update each cron-job.org job's Authorization header with the
  new token.

## Rotating the PAT

- GitHub emails you before a fine-grained PAT expires.
- When you rotate: generate a new PAT with the same scope. Update each
  cron-job.org job's Authorization header. Keep the old PAT active for
  an hour as a safety window. Then delete the old PAT.

## Why cron-job.org specifically

- Free tier is generous (50 cron jobs, 1-minute precision) and doesn't
  require a payment method.
- Supports America/Chicago timezone natively — you don't have to
  dual-enter UTC hours for DST.
- HTTPS-only, supports custom headers and JSON bodies. Fires at the
  minute mark, no queue.

Alternatives: EasyCron (free tier with ads), temporal (overkill),
your own VPS with crontab (reliable but more operations surface).

## What this does NOT solve

- **Workflow runtime**: once dispatched, the workflow itself still takes
  its normal time (setup + install + run). This is about when the run
  STARTS, not how long it takes.
- **GitHub Actions outages**: if GitHub itself is down, workflows can't
  run regardless of when they're dispatched. Low probability; no
  mitigation here.
- **The engine's math layer**: wholly unrelated. Scheduling reliability
  vs. output quality are separate axes.

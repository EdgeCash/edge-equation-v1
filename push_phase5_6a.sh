#!/usr/bin/env bash
# push_phase5_6a.sh
#
# Runs apply_phase5_6a.sh, and if tests pass, commits and pushes to
# branch phase-5-6a-api-website and prints the PR URL.
#
# Run from the repo root of edge-equation-v1.

set -euo pipefail

BRANCH="phase-5-6a-api-website"
BASE_BRANCH="main"

if [ ! -f "apply_phase5_6a.sh" ]; then
  echo "ERROR: apply_phase5_6a.sh not found in current directory." >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not inside a git repository." >&2
  exit 1
fi

echo ">> Fetching latest from origin"
git fetch origin

echo ">> Ensuring $BASE_BRANCH is up to date"
git checkout "$BASE_BRANCH"
git pull origin "$BASE_BRANCH"

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo ">> Branch $BRANCH exists locally, checking out"
  git checkout "$BRANCH"
else
  echo ">> Creating branch $BRANCH from $BASE_BRANCH"
  git checkout -b "$BRANCH"
fi

echo ""
echo ">> Running apply_phase5_6a.sh"
chmod +x apply_phase5_6a.sh
if ! ./apply_phase5_6a.sh; then
  echo ""
  echo "ERROR: apply_phase5_6a.sh failed." >&2
  exit 1
fi

echo ""
echo ">> Staging and committing"
git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit. Phase 5+6A may already be in place on this branch."
else
  git commit -m "Phase 5 + 6A – API layer and website skeleton

Exposes the deterministic engine through a FastAPI service and stands
up the public-facing Next.js website as a monorepo sibling.

Phase 5 – API (read-only, no auth, no DB, no external I/O):
- api/main.py + create_app() factory
- api/data_source.py – live mock sources per request; tests pin datetime
    picks_for_today / premium_picks_for_today / slate_entries_for_sport
    Sport aliases (mlb/MLB/nba/Nhl/soccer) resolved to canonical league.
- api/schemas/{health,pick,card,premium_pick}.py – pydantic models
- api/routers/{health,picks,cards,premium,slate}.py
    GET /health                   -> {status: ok, version: v1}
    GET /picks/today              -> list[Pick flat dicts]
    GET /cards/daily              -> daily_edge card
    GET /premium/picks/today      -> list[PremiumPick flat dicts]
    GET /premium/cards/daily      -> premium_daily_edge card
    GET /slate/{sport}            -> list[slate entries]; 404 on unknown

Sport/league resolution matches engine: NBA maps to NCAA_Basketball
(Phase 2 config carry-over). Routes skip markets whose math isn't wired
(handled by Phase-4A slate_runner).

Phase 6A – Website skeleton (Next.js 14 + TypeScript + Tailwind):
- website/pages/_app.tsx, index.tsx, daily-edge.tsx, premium-edge.tsx,
    about.tsx, contact.tsx
- website/components/{Layout,Header,Footer,CardShell}.tsx
- website/styles/globals.css – Tailwind layers + font imports
- website/tailwind.config.js – dark-mode class strategy, custom palette
    ink (near-black scale) + edge.accent warm gold (#d7b572)
- website/tsconfig.json with @/ path alias
- vercel.json – monorepo deploy config pointing at website/ as root

Design: editorial / restrained. Fraunces display + Inter Tight body +
JetBrains Mono labels. Corner tick-marks on cards. Tabular numerals
for data. Subtle radial backgrounds. Facts. Not Feelings.

No API calls from website yet (Phase 6B wires this up).
NEXT_PUBLIC_API_URL env var reserved.

Tests:
- tests_api/conftest.py – pins datetime to 2026-04-20 via monkeypatch
- tests_api/test_{health,picks,cards,premium,slate}.py – 24 tests total
- Includes formula-truth assertion on DET @ BOS ML via API
- All deterministic; no clock dependency

Engine/ingestion/publisher/premium layers untouched.
All 43 existing tests still green."
fi

echo ""
echo ">> Pushing $BRANCH to origin"
git push -u origin "$BRANCH"

echo ""
echo "================================================================"
echo "Push complete. Open the PR by clicking this URL:"
echo ""
echo "  https://github.com/EdgeCash/edge-equation-v1/pull/new/$BRANCH"
echo ""
echo "Suggested PR title:"
echo "  Phase-5 + 6A: FastAPI Layer + Next.js Website Skeleton"
echo ""
echo "GitHub will prefill the description from the commit message."
echo "================================================================"

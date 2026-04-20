#!/usr/bin/env bash
# push_phase4bc.sh
#
# Runs apply_phase4bc.sh, and if tests pass, commits and pushes to
# branch phase-4bc-publisher-premium and prints the PR URL.
#
# Run from the repo root of edge-equation-v1.

set -euo pipefail

BRANCH="phase-4bc-publisher-premium"
BASE_BRANCH="main"

if [ ! -f "apply_phase4bc.sh" ]; then
  echo "ERROR: apply_phase4bc.sh not found in current directory." >&2
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
echo ">> Running apply_phase4bc.sh"
chmod +x apply_phase4bc.sh
if ! ./apply_phase4bc.sh; then
  echo ""
  echo "ERROR: apply_phase4bc.sh failed." >&2
  exit 1
fi

echo ""
echo ">> Staging and committing"
git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit. Phase 4B+4C may already be in place on this branch."
else
  git commit -m "Phase 4B+4C – publisher and premium layer

Adds a deterministic publisher layer and a premium analytics layer
on top of the existing Phase-4A engine + ingestion.

Publisher modules (4B):
- src/edge_equation/publishing/base_publisher.py
    PublishResult frozen dataclass + BasePublisher Protocol
- src/edge_equation/publishing/x_publisher.py
    XPublisher with 280-char truncation (ellipsis)
- src/edge_equation/publishing/discord_publisher.py
    DiscordPublisher with embed-style payload builder
- src/edge_equation/publishing/email_publisher.py
    EmailPublisher with subject/body formatting
- src/edge_equation/publishing/publish_runner.py
    publish_daily_edge / publish_evening_edge / publish_card
    Orchestrates X + Discord + Email; failures captured per-target.

Premium modules (4C):
- src/edge_equation/premium/mc_simulator.py
    MonteCarloSimulator with fixed-seed determinism
    simulate_binary / simulate_total -> {p10, p50, p90, mean}
- src/edge_equation/premium/premium_pick.py
    PremiumPick frozen wrapper around Pick
- src/edge_equation/premium/premium_formatter.py
    format_premium_pick -> flat dict
- src/edge_equation/premium/premium_cards.py
    build_premium_daily_edge_card / build_premium_overseas_edge_card

All publishers:
- No real network I/O (X, Discord webhook, SMTP all stubbed)
- Dry-run returns immediately; non-dry-run simulates with fake message_id
- Return PublishResult on failure rather than raising

All premium:
- Deterministic MC (random.Random seeded per call)
- Immutable PremiumPick wrapping the existing Pick schema (no changes to Pick)
- Pure formatting for card payloads, shared 'Facts. Not Feelings.' tagline

Tests (8 new files, 43+ tests):
- tests/test_publishing_base.py
- tests/test_publishing_x.py
- tests/test_publishing_discord.py
- tests/test_publishing_email.py
- tests/test_publish_runner.py
- tests/test_premium_mc_simulator.py
- tests/test_premium_pick_and_formatter.py
- tests/test_premium_cards.py

Example publish_daily_edge(dry_run=True) returns 3 PublishResults (x, discord, email),
all success=True. Example MC binary sim for fair_prob=0.618133 @ seed=42, n=1000:
p10=0.601666, p50=0.610567, p90=0.634615, mean=0.597000."
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
echo "  Phase-4B+4C: Publisher Layer + Premium Analytics (MC + Distributions)"
echo ""
echo "GitHub will prefill the description from the commit message."
echo "================================================================"

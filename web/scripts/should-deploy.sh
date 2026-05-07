#!/usr/bin/env bash
# Vercel "Ignored Build Step" gate.
#
# Vercel runs this script before kicking off a deployment. The exit
# code controls whether the build proceeds:
#
#   exit 1 -> build (something the website cares about changed)
#   exit 0 -> skip  (nothing the website renders changed)
#
# We hit Vercel's free-plan 100-deploys-per-day cap because the repo
# pushes constantly: closing-line snapshots, PrizePicks fetcher,
# Daily Master commits, plus every PR preview. The vast majority of
# those commits don't change anything the rendered site reads.
#
# This script lets through only the diffs that actually matter for
# the deployed website:
#
#   web/                     - the Next.js app source itself
#   website/public/          - the data drop the build copies in
#   package.json             - dependency/lockfile changes
#   package-lock.json        - dependency/lockfile changes
#
# Everything else (data/closing_lines/, data/prizepicks/, src/,
# tests/, .github/, docs/, etc.) is engine-side and shouldn't
# trigger a Vercel rebuild.
#
# Set this script as the project's Ignored Build Step in the Vercel
# dashboard:
#
#   Project Settings -> Git -> Ignored Build Step
#   Command: bash web/scripts/should-deploy.sh
#
# If the user is overriding (e.g. a manual redeploy or branch other
# than main), Vercel's $VERCEL_GIT_COMMIT_REF and $VERCEL_GIT_PREVIOUS_SHA
# tell us the situation. We always build for non-main branches so
# preview deployments still work for PR review.

set -euo pipefail

# Always build on PR previews / non-main branches so reviewers see
# the changes regardless of what files moved.
if [ "${VERCEL_GIT_COMMIT_REF:-}" != "main" ]; then
  echo "Branch is '${VERCEL_GIT_COMMIT_REF:-unknown}' (not main) - building."
  exit 1
fi

# Vercel exposes the previous commit it deployed; default to HEAD~1
# when missing (first deploy on a branch).
PREV="${VERCEL_GIT_PREVIOUS_SHA:-HEAD~1}"
HEAD_SHA="${VERCEL_GIT_COMMIT_SHA:-HEAD}"

# `git diff --quiet` exits 0 when the diff is empty (no changes in
# the listed paths) and 1 when there are changes. Negate that to
# build only when at least one of these paths is touched.
if git diff --quiet "$PREV" "$HEAD_SHA" -- \
     web/ \
     website/public/ \
     package.json \
     package-lock.json
then
  echo "No web/, website/public/, or dep-manifest changes between"
  echo "  $PREV..$HEAD_SHA"
  echo "Skipping Vercel deployment."
  exit 0
fi

echo "Web-relevant changes detected between $PREV..$HEAD_SHA - building."
exit 1

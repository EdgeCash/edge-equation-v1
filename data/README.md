# Edge Equation — Milestone 1: Ledger

This package adds the immutable pick ledger to the repo. Unzip this
folder and its contents go directly into the repo at the matching
paths — the folder structure inside this zip mirrors the repo layout.

## What's in here

    src/edge_equation/ledger/
        __init__.py
        schema.py       — table definitions and init_ledger()
        writer.py       — log_pick, log_result, log_closing_line, etc.
        reader.py       — stats_posted, grade_calibration, list_picks, etc.

    scripts/
        ledger_sanity_check.py
                        — standalone script that exercises the full
                          ledger end-to-end, prints every read function.
                          Safe to run anytime, cleans up after itself.

    data/
        README.md       — placeholder so the folder exists in git.
                          The actual ledger.db file is git-ignored.

## How to install

1. Download and unzip this archive.
2. Copy or drag the contents into the repo, preserving folder structure:
     src/edge_equation/ledger/  →  existing src/edge_equation/
     scripts/                   →  repo root (create if needed)
     data/                      →  repo root (create if needed)

3. Add this line to your .gitignore if it isn't there already:
     data/ledger.db

4. Commit on a branch — suggestion: `feature/ledger`.

## What this does not do yet

- Nothing in the engine currently writes to the ledger. The ledger is
  standalone foundation. The next milestone will wire the engine's
  picks into log_pick() so every graded play is captured.
- Nothing reads from the ledger for display yet either. The transparency
  footer, public ledger page, and results card will all use reader.py
  in future milestones.

## The three rules the ledger enforces

1. Once a pick is logged, its analytical fields never change.
2. Picks can be flagged as voided (with a reason) but never deleted.
3. Every pick, result, and closing line is timestamped UTC and
   attributed to an engine_version string.

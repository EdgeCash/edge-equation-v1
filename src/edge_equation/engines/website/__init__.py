"""Website exporters — engine ledgers → public JSON.

Free public-release mode (decided 2026-05-01): all picks ship to the
website as a public, append-only track record. No paywall. The
exporters in this package read the per-engine settled-pick ledgers
out of DuckDB and emit JSON files into ``website/public/data/`` that
the Next.js site loads at build time.

Modules
~~~~~~~

* ``build_track_record.py`` — walks the per-engine ``*_pick_settled``
  tables, filters to LEAN-and-above (NO_PLAY excluded), and writes
  ``track-record/ledger.json`` (every pick), ``summary.json`` (running
  record per tier × engine), and ``by-day.json`` (per-day rollup for
  the chart strip).

* ``build_daily_feed.py`` — reads today's NRFI predictions + game
  metadata out of DuckDB and writes ``daily/latest.json`` in the
  schema documented at ``website/public/data/daily/README.md``. NRFI
  only for now; Props/Full-Game/Parlay engines extend this once they
  pass their own sanity gates.

The exporters are pure read operations — they never mutate the
DuckDB. Run them after the daily settlement cron so the website
publishes settled outcomes alongside today's open picks.
"""


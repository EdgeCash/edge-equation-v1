# Edge Equation v1

**Facts. Not Feelings.** — deterministic sports analytics engine.

## Architecture

```
src/edge_equation/
├── auth/             # Stripe + sessions + tokens (paid premium gate)
├── compliance/       # Disclaimers + sanitiser + content rules
├── config/           # Sport configs, market registry, tuning knobs
├── context/          # Officials, weather, travel, rest, situational
├── engine/           # Slate runner, betting engine, pick schema
├── engines/          # ─── Sport / market specific engines (canonical home) ───
│   ├── nrfi/         #     MLB first-inning NRFI/YRFI (production, flagship)
│   ├── props_prizepicks/   # MLB player props via The Odds API (in dev)
│   └── full_game/    #     MLB ML / Total / F5 / Run Line (in dev)
├── ingestion/        # Game/market sources, Odds API client, normaliser
├── math/             # Decimal-precision deterministic primitives
│                       (Bradley-Terry, Poisson, Dixon-Coles, isotonic,
│                        kelly_adaptive, monte_carlo, scoring, decay)
├── persistence/      # SQLite/Turso store, slate cache, ledger
├── posting/          # Card formatters, premium daily body, ledger recap
├── publishing/       # X / Discord / email publishers + failsafe
├── stats/            # Team strength, ELO, season stats ingest
└── utils/            # Shared logging, helpers
```

Top-level:

- `api/` — FastAPI app (picks, slate, premium, NRFI board endpoints, auth, cron)
- `tests/` — slim pytest suite (no fastapi, no heavy ML extras)
- `tests_api/` — fastapi-dependent tests
- `data/` — CSV snapshots + DuckDB caches (gitignored runtime artifacts)
- `tools/` — diagnostics + sheets utilities
- `web/` — Next.js dashboard (Vercel root directory)
- `website/public/data/` — daily cron output consumed by `web/` at build time
- `.github/workflows/` — daily cron jobs (slate runs, refresh, settle, NRFI email)

## Engines

The **NRFI engine** is the canonical reference implementation. New engines
copy its patterns:

1. **Feature builder** — sport/market-specific layers + shrinkage.
2. **Model bundle** — XGBoost classifier + Poisson regressor + isotonic
   calibration + SHAP explainer. Bridge gracefully falls back to the
   deterministic Poisson baseline when no trained bundle is on disk.
3. **Output payload** — single canonical `Output` dataclass with adapters
   for email, API, dashboard, posting card. One shape, every consumer.
4. **Integration bridge** — single import surface from `src/edge_equation/`
   into the engine, so the deterministic core never depends on optional
   ML extras.

**Tier classification** is unified across engines:

| Engine | Basis | Reason |
|---|---|---|
| NRFI / YRFI | Raw probability (≥70 LOCK, 64–69 STRONG, 58–63 MODERATE, 55–57 LEAN) | Market is symmetric (~50/50, both sides at -110), so probability and edge are interchangeable |
| Props, Full-Game | Edge in pp (≥8 LOCK, 5–8 STRONG, 3–5 MODERATE, 1–3 LEAN) | Non-symmetric markets (favorites at -150, props at -120) require edge-based thresholds to be meaningful |

## Quick start

```bash
# Slim install (deterministic core only)
pip install -e .[dev]

# Full elite stack (xgboost / lightgbm / shap / pybaseball / duckdb / etc.)
pip install -e .[nrfi]

# Run the deterministic slate engine
python -m edge_equation                     # today's slate
python -m edge_equation --date 2026-04-28

# Run the NRFI engine
python -m edge_equation.engines.nrfi.run_daily
python -m edge_equation.engines.nrfi.backtest_historical 2026-04-01 2026-04-27 \
    --use-model --forecast-weather --green-only-roi --reliability-summary

# Run the standalone NRFI/YRFI daily email (also wired into a 9am CT cron)
python -m edge_equation.engines.nrfi.email_report --dry-run
```

## Daily Operations

**Public-testing release runs in manual-trigger mode** (since 2026-05-01).
Daily user-facing crons are commented out; the operator triggers each
release via the GitHub Actions UI after confirming lineups, weather,
and umpires are posted.

See [`docs/MANUAL_DAILY_RELEASE.md`](docs/MANUAL_DAILY_RELEASE.md) for
the step-by-step SOP.

## Tests

```bash
python -m pytest tests/         # slim suite (no fastapi)
python -m pytest tests_api/     # fastapi-dependent tests
python -m pytest                # everything
```

## Brand

> **Edge Equation — Facts. Not Feelings.**

No emojis, no hype, no hashtags in body copy, mandatory disclaimer footer
on all published content. The deterministic core enforces this through
`posting/posting_formatter.py` and `compliance/sanitizer.py`.

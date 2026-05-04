"""
Per-market rolling backtest — stub.

Replace verbatim during cutover:

    curl -fsSL https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/exporters/mlb/backtest.py \\
        -o src/edge_equation/exporters/mlb/backtest.py

The thin replay tool already in v1 (src/edge_equation/backtest/cli.py)
covers the regression-diagnosis use case using scrapers' picks_log.json
schema. This module is the production rolling backtest that the daily
orchestrator calls in step 5 to (a) fit calibration constants and (b)
emit the summary that gates.market_gate consumes.
"""

"""
Closing-line snapshot — stub.

Will be replaced verbatim during cutover by curl-ing
edge-equation-scrapers/exporters/mlb/closing_snapshot.py:

    curl -fsSL https://raw.githubusercontent.com/EdgeCash/edge-equation-scrapers/main/exporters/mlb/closing_snapshot.py \\
        -o src/edge_equation/exporters/mlb/closing_snapshot.py

Until then the closing-lines workflow short-circuits with a no-op so a
mistakenly-enabled cron doesn't blow up.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true", default=False)
    parser.parse_args(argv)
    print("[closing_snapshot] stub — port from scrapers before enabling")
    return 0


if __name__ == "__main__":
    sys.exit(main())

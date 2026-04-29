"""Recover or rebuild the full NRFI historical corpus.

Priority order:

1. If Cloudflare R2 credentials are present, try likely corpus prefixes and
   download every object under the first prefix that exists.
2. If R2 is not configured or no corpus objects exist, run the existing
   resumable historical backfill in bounded chunks.

The script is intentionally conservative: it never needs secrets in code, and
it can be rerun safely because both R2 downloads and DuckDB backfill operations
are idempotent/resumable.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger
from edge_equation.utils.object_storage import R2Client

from ..config import get_default_config
from .backfill import HistoricalBackfillReport, backfill_historical_data

log = get_logger(__name__)

DEFAULT_CORPUS_PREFIXES: tuple[str, ...] = (
    "nrfi/corpus/",
    "nrfi/full_corpus/",
    "nrfi/backfill/",
    "nrfi/data/",
)


@dataclass
class CorpusRecoveryReport:
    """Outcome of one recovery run."""

    source: str
    downloaded_keys: list[str] = field(default_factory=list)
    backfill_reports: list[HistoricalBackfillReport] = field(default_factory=list)
    message: str = ""

    @property
    def downloaded_count(self) -> int:
        return len(self.downloaded_keys)

    def summary(self) -> str:
        lines = [
            "NRFI corpus recovery report",
            "-" * 56,
            f"  source              {self.source}",
            f"  downloaded objects  {self.downloaded_count}",
            f"  backfill chunks     {len(self.backfill_reports)}",
        ]
        if self.message:
            lines.append(f"  note                {self.message}")
        for report in self.backfill_reports:
            lines.extend(["", report.summary()])
        return "\n".join(lines)


def recover_corpus(
    *,
    destination: str | Path = "data/full_corpus",
    prefixes: Iterable[str] = DEFAULT_CORPUS_PREFIXES,
    fallback_start: str = "2025-03-27",
    fallback_end: Optional[str] = None,
    chunk_days: int = 14,
    include_odds: bool = False,
) -> CorpusRecoveryReport:
    """Recover corpus from R2 if possible, otherwise rebuild via APIs."""

    dest = Path(destination)
    client = R2Client.from_env()
    if client is not None:
        for prefix in prefixes:
            keys = client.list_keys(prefix)
            if not keys:
                continue
            downloaded: list[str] = []
            for key in keys:
                rel = key[len(prefix):].lstrip("/")
                if not rel:
                    continue
                target = dest / rel
                client.download_file(key, target)
                downloaded.append(key)
            return CorpusRecoveryReport(
                source="r2",
                downloaded_keys=downloaded,
                message=f"downloaded prefix {prefix} to {dest}",
            )

    end = fallback_end or date.today().isoformat()
    if client is None and Path(destination) == Path("data/full_corpus"):
        # The default fallback backfill writes to the engine's configured DuckDB
        # cache, not to data/full_corpus. Keeping the default destination for R2
        # downloads avoids surprising local file layouts.
        dest = get_default_config().resolve_paths().cache_dir
    reports = _run_fallback_backfill(
        fallback_start=fallback_start,
        fallback_end=end,
        chunk_days=chunk_days,
        include_odds=include_odds,
    )
    return CorpusRecoveryReport(
        source="api_backfill",
        backfill_reports=reports,
        message="R2 corpus unavailable; used resumable API backfill",
    )


def _run_fallback_backfill(
    *,
    fallback_start: str,
    fallback_end: str,
    chunk_days: int,
    include_odds: bool,
) -> list[HistoricalBackfillReport]:
    reports: list[HistoricalBackfillReport] = []
    cur = date.fromisoformat(fallback_start)
    end = date.fromisoformat(fallback_end)
    cfg = get_default_config().resolve_paths()
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        report = backfill_historical_data(
            cur.isoformat(),
            chunk_end.isoformat(),
            config=cfg,
            include_odds=include_odds,
            skip_completed=True,
        )
        reports.append(report)
        cur = chunk_end + timedelta(days=1)
    return reports


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recover NRFI full corpus from R2 or rebuild via APIs."
    )
    parser.add_argument("--destination", default="data/full_corpus")
    parser.add_argument("--prefix", action="append", dest="prefixes")
    parser.add_argument("--fallback-from", default="2025-03-27")
    parser.add_argument("--fallback-to", default=date.today().isoformat())
    parser.add_argument("--chunk-days", type=int, default=14)
    parser.add_argument("--include-odds", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = recover_corpus(
        destination=args.destination,
        prefixes=tuple(args.prefixes) if args.prefixes else DEFAULT_CORPUS_PREFIXES,
        fallback_start=args.fallback_from,
        fallback_end=args.fallback_to,
        chunk_days=args.chunk_days,
        include_odds=args.include_odds,
    )
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())

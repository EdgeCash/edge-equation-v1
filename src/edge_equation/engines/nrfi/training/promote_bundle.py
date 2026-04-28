"""Promote a trained NRFI bundle to R2.

CLI helper invoked by the walkforward / weekly-retrain workflows AFTER
the sanity gate has passed. Reads the bundle from `cfg.model_dir`,
packs it into a tarball, and uploads to two R2 keys::

    nrfi/bundles/nrfi_bundle_YYYYMMDD_v1.bundle    # date-stamped archive
    nrfi/bundles/latest.bundle                      # canonical pointer

All inference paths (`NRFIEngineBridge.try_load`) read `latest.bundle`
when their local `cfg.model_dir` is empty, so this command flips the
production model.

Usage::

    python -m edge_equation.engines.nrfi.training.promote_bundle
    python -m edge_equation.engines.nrfi.training.promote_bundle --date 2026-04-28

Exit codes:
    0  bundle uploaded successfully (or --dry-run)
    1  precondition failed (no model_dir, no R2 creds, ...)
    2  upload error after preconditions passed
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date
from pathlib import Path
from typing import Optional, Sequence

from edge_equation.utils.logging import get_logger
from edge_equation.utils.object_storage import (
    R2Client,
    upload_nrfi_bundle,
)

from ..config import get_default_config

log = get_logger(__name__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote a trained NRFI bundle to R2"
    )
    parser.add_argument(
        "--date", default=_date.today().isoformat(),
        help="Date stamp for the archive key (default: today UTC).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate preconditions and report what would be uploaded; "
              "do not actually upload.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    cfg = get_default_config().resolve_paths()
    model_dir = Path(cfg.model_dir)

    # Precondition 1: bundle files exist on disk
    expected = list(model_dir.glob("*.pkl")) + list(model_dir.glob("*.json"))
    if not expected:
        log.error(
            "No bundle files found in %s — has walkforward training run?",
            model_dir,
        )
        return 1
    log.info("Bundle artifacts to pack:")
    for f in sorted(expected):
        log.info("  %s (%d bytes)", f.name, f.stat().st_size)

    # Precondition 2: R2 client constructible
    r2 = R2Client.from_env()
    if r2 is None:
        log.error(
            "R2 client unavailable. Confirm R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL are set in env.",
        )
        return 1

    if args.dry_run:
        log.info("Dry run — would upload to bucket=%s endpoint=%s",
                 r2.bucket, r2.endpoint_url)
        return 0

    try:
        date_key, latest_key = upload_nrfi_bundle(r2, model_dir, args.date)
    except Exception as e:
        log.exception("R2 upload failed: %s", e)
        return 2

    log.info("Bundle promoted:")
    log.info("  date-stamped: s3://%s/%s", r2.bucket, date_key)
    log.info("  latest:       s3://%s/%s", r2.bucket, latest_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())

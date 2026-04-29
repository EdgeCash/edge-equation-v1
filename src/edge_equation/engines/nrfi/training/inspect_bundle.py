"""Bundle inspection / validation CLI.

Loads the trained NRFI bundle (auto-fetching from R2 when local cache
is empty), then prints a human-readable report covering:

* Bundle provenance — model version, feature count, local file
  timestamps, R2 last-modified date when available.
* Sanity comparison — Brier / log-loss / accuracy of the ML head vs.
  the deterministic Poisson baseline on a configurable trailing
  window of actual games.
* Probability distribution — histogram of NRFI predictions bucketed
  by tier so the operator can eyeball "are we generating any LOCKs?"
  before running the daily report.

Usage::

    python -m edge_equation.engines.nrfi.training.inspect_bundle
    python -m edge_equation.engines.nrfi.training.inspect_bundle --season 2026 --json
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from ..config import NRFIConfig, get_default_config


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BundleProvenance:
    loaded: bool
    model_version: str = ""
    feature_count: int = 0
    feature_names: list[str] = field(default_factory=list)
    model_dir: str = ""
    local_files: dict[str, str] = field(default_factory=dict)  # name → mtime ISO
    r2_last_modified: Optional[str] = None
    source: str = "unknown"  # "local" | "r2" | "missing"


@dataclass
class TierHistogramRow:
    tier: str
    band: str
    count: int


@dataclass
class InspectReport:
    provenance: BundleProvenance
    sanity_summary: Optional[str] = None
    sanity_passed: Optional[bool] = None
    tier_histogram: list[TierHistogramRow] = field(default_factory=list)
    probability_window: Optional[tuple[str, str]] = None
    n_predictions_in_window: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bundle provenance
# ---------------------------------------------------------------------------


def _local_file_mtimes(model_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not model_dir.exists():
        return out
    for child in sorted(model_dir.iterdir()):
        if not child.is_file():
            continue
        ts = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        out[child.name] = ts.isoformat(timespec="seconds")
    return out


def _r2_last_modified() -> Optional[str]:
    try:
        from edge_equation.utils.object_storage import (
            NRFI_LATEST_KEY, R2Client,
        )
        r2 = R2Client.from_env()
        if r2 is None:
            return None
        ts = r2.last_modified(NRFI_LATEST_KEY)
        if ts is None:
            return None
        if hasattr(ts, "isoformat"):
            return ts.isoformat(timespec="seconds")
        return str(ts)
    except Exception:
        return None


def collect_provenance(cfg: NRFIConfig) -> BundleProvenance:
    """Build a BundleProvenance, auto-loading the bundle via the bridge.

    The bridge's `try_load` populates `cfg.model_dir` from R2 when the
    local cache is empty, so calling it here gives us a faithful
    snapshot of what the daily run would see.
    """
    from ..integration.engine_bridge import NRFIEngineBridge

    bridge = NRFIEngineBridge.try_load(cfg)
    prov = BundleProvenance(loaded=bridge.available(),
                              model_dir=str(cfg.model_dir))
    prov.local_files = _local_file_mtimes(Path(cfg.model_dir))
    prov.r2_last_modified = _r2_last_modified()
    prov.source = (
        "local" if (bridge.available() and prov.local_files) else
        ("r2" if bridge.available() else "missing")
    )

    # Pull metadata directly off the loaded bundle if available.
    engine = getattr(bridge, "_engine", None)
    bundle = getattr(engine, "_bundle", None) if engine is not None else None
    if bundle is None:
        bundle = getattr(engine, "bundle", None) if engine is not None else None
    if bundle is not None:
        prov.model_version = getattr(bundle, "model_version", "") or ""
        names = list(getattr(bundle, "feature_names", []) or [])
        prov.feature_count = len(names)
        prov.feature_names = names
    return prov


# ---------------------------------------------------------------------------
# Sanity comparison + tier histogram
# ---------------------------------------------------------------------------


def _sanity_summary(cfg: NRFIConfig, season: int) -> tuple[Optional[str], Optional[bool]]:
    """Run `compute_sanity` and return (summary_text, passed).

    Returns (None, None) when extras aren't installed or DuckDB has
    no historical data yet.
    """
    try:
        from .sanity import compute_sanity
        from ..data.storage import NRFIStore
    except ImportError:
        return None, None
    try:
        store = NRFIStore(cfg.duckdb_path)
        report = compute_sanity(store=store, season=season, config=cfg)
    except Exception as e:
        return f"sanity computation skipped ({type(e).__name__}: {e})", None
    if report is None:
        return "sanity report unavailable", None
    return report.summary(), bool(getattr(report, "passed", False))


def _tier_histogram_for_today(cfg: NRFIConfig) -> tuple[
    list[TierHistogramRow], Optional[tuple[str, str]], int,
]:
    """Bucket today's predictions by the higher-tier side per game so
    the operator can confirm "are LOCK / STRONG tiers actually getting
    generated today?" before sending the email."""
    try:
        from datetime import date as _date
        from ..data.storage import NRFIStore
        from edge_equation.engines.tiering import Tier, classify_tier
    except ImportError:
        return [], None, 0
    try:
        today = _date.today().isoformat()
        store = NRFIStore(cfg.duckdb_path)
        df = store.predictions_for_date(today)
    except Exception:
        return [], None, 0

    if df is None or df.empty:
        return [], (today, today), 0

    counts: dict[str, int] = {t.value: 0 for t in Tier}
    for _, row in df.iterrows():
        nrfi_pct = row.get("nrfi_pct")
        if nrfi_pct is None:
            continue
        nrfi_p = float(nrfi_pct) / 100.0
        n_clf = classify_tier(market_type="NRFI", side_probability=nrfi_p)
        y_clf = classify_tier(market_type="YRFI",
                                side_probability=1.0 - nrfi_p)
        winning = n_clf if _tier_rank(n_clf.tier) >= _tier_rank(y_clf.tier) else y_clf
        counts[winning.tier.value] += 1

    band_labels = {
        Tier.LOCK.value:     "≥70%",
        Tier.STRONG.value:   "64-69%",
        Tier.MODERATE.value: "58-63%",
        Tier.LEAN.value:     "55-57%",
        Tier.NO_PLAY.value:  "<55%",
    }
    rows = [
        TierHistogramRow(tier=t.value, band=band_labels[t.value],
                            count=counts.get(t.value, 0))
        for t in (Tier.LOCK, Tier.STRONG, Tier.MODERATE,
                    Tier.LEAN, Tier.NO_PLAY)
    ]
    return rows, (today, today), int(len(df))


def _tier_rank(tier) -> int:
    from edge_equation.engines.tiering import Tier
    return {Tier.LOCK: 4, Tier.STRONG: 3, Tier.MODERATE: 2,
            Tier.LEAN: 1, Tier.NO_PLAY: 0}[tier]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_report(*, season: Optional[int] = None,
                   config: Optional[NRFIConfig] = None) -> InspectReport:
    cfg = (config or get_default_config()).resolve_paths()
    prov = collect_provenance(cfg)

    notes: list[str] = []
    if not prov.loaded:
        notes.append(
            "Bundle did not load — daily runs will fall back to the "
            "Poisson baseline. Set R2_* env vars or place a bundle "
            f"in {cfg.model_dir}.",
        )

    sanity_text, sanity_passed = _sanity_summary(
        cfg, season or datetime.now(timezone.utc).year,
    )
    histogram, window, n_preds = _tier_histogram_for_today(cfg)
    if n_preds == 0:
        notes.append(
            "No predictions on file for today — run the daily ETL + "
            "engine pass to populate predictions before inspection "
            "can show a tier histogram.",
        )

    return InspectReport(
        provenance=prov,
        sanity_summary=sanity_text,
        sanity_passed=sanity_passed,
        tier_histogram=histogram,
        probability_window=window,
        n_predictions_in_window=n_preds,
        notes=notes,
    )


def render_report(report: InspectReport) -> str:
    p = report.provenance
    lines: list[str] = []
    lines.append("NRFI Bundle Inspection")
    lines.append("=" * 60)
    lines.append("")
    lines.append("PROVENANCE")
    lines.append("-" * 60)
    lines.append(f"  loaded            {p.loaded}")
    lines.append(f"  source            {p.source}")
    lines.append(f"  model_version     {p.model_version or '(none)'}")
    lines.append(f"  feature_count     {p.feature_count}")
    lines.append(f"  model_dir         {p.model_dir}")
    if p.r2_last_modified:
        lines.append(f"  R2 last-modified  {p.r2_last_modified}")
    if p.local_files:
        lines.append("  local files:")
        for name, mtime in p.local_files.items():
            lines.append(f"    {name:<40} {mtime}")
    lines.append("")

    if report.sanity_summary:
        lines.append("SANITY (ML vs Poisson baseline)")
        lines.append("-" * 60)
        lines.append(report.sanity_summary)
        if report.sanity_passed is not None:
            verdict = "PASS" if report.sanity_passed else "FAIL"
            lines.append(f"  gate: {verdict}")
        lines.append("")

    if report.tier_histogram:
        lines.append(
            "TIER HISTOGRAM "
            f"({report.probability_window[0] if report.probability_window else '?'})"
        )
        lines.append("-" * 60)
        lines.append(f"  total predictions     {report.n_predictions_in_window}")
        for row in report.tier_histogram:
            lines.append(
                f"    {row.tier:<10} {row.band:<10} {row.count:>4}"
            )
        lines.append("")

    if report.notes:
        lines.append("NOTES")
        lines.append("-" * 60)
        for n in report.notes:
            lines.append(f"  • {n}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Inspect the trained NRFI bundle (auto-fetches from R2)",
    )
    parser.add_argument("--season", type=int, default=None,
                          help="Season year for the sanity comparison "
                              "(default: current year UTC).")
    parser.add_argument("--json", action="store_true",
                          help="Emit a JSON report instead of the text table.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = build_report(season=args.season)

    if args.json:
        payload = asdict(report)
        # Drop the noisy feature_names list from the JSON dump to keep
        # the report operator-readable.
        payload.get("provenance", {}).pop("feature_names", None)
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_report(report))
    return 0 if report.provenance.loaded else 1


if __name__ == "__main__":
    sys.exit(main())

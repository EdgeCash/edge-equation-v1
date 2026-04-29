"""Tests for the NRFI bundle inspection CLI.

The inspector touches the engine bridge, the sanity report module,
and the predictions table. We monkey-patch each so the suite stays
runnable without duckdb / xgboost / a real R2 bucket.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from edge_equation.engines.nrfi.training import inspect_bundle as ib


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeBundle:
    model_version: str = "nrfi_v3"
    feature_names: list = None
    def __post_init__(self):
        if self.feature_names is None:
            self.feature_names = [f"f{i}" for i in range(42)]


class _FakeEngine:
    def __init__(self, bundle):
        self._bundle = bundle


class _FakeBridge:
    """Stands in for NRFIEngineBridge — minimal interface."""
    def __init__(self, *, available: bool, bundle=None):
        self._available = available
        self._engine = _FakeEngine(bundle) if bundle is not None else None
    def available(self) -> bool:
        return self._available


# ---------------------------------------------------------------------------
# collect_provenance
# ---------------------------------------------------------------------------


def test_collect_provenance_when_bundle_loaded(monkeypatch, tmp_path):
    """A loaded bundle yields source='local' (when files exist) plus
    model_version + feature count from the bundle."""
    # Touch a couple of fake artifact files so the local_files dict
    # is populated.
    (tmp_path / "v3_classifier.pkl").write_bytes(b"\x00")
    (tmp_path / "v3_features.json").write_text("[]")

    fake_bridge = _FakeBridge(available=True, bundle=_FakeBundle())

    monkeypatch.setattr(
        "edge_equation.engines.nrfi.integration.engine_bridge."
        "NRFIEngineBridge.try_load",
        classmethod(lambda cls, config=None: fake_bridge),
    )

    @dataclass
    class _Cfg:
        model_dir: str
        duckdb_path: str = "/tmp/nrfi.duckdb"
        def resolve_paths(self): return self

    prov = ib.collect_provenance(_Cfg(model_dir=str(tmp_path)))
    assert prov.loaded is True
    assert prov.model_version == "nrfi_v3"
    assert prov.feature_count == 42
    assert prov.source == "local"
    assert prov.model_dir == str(tmp_path)
    assert "v3_classifier.pkl" in prov.local_files
    assert "v3_features.json" in prov.local_files


def test_collect_provenance_when_bundle_missing(monkeypatch, tmp_path):
    fake_bridge = _FakeBridge(available=False)
    monkeypatch.setattr(
        "edge_equation.engines.nrfi.integration.engine_bridge."
        "NRFIEngineBridge.try_load",
        classmethod(lambda cls, config=None: fake_bridge),
    )

    @dataclass
    class _Cfg:
        model_dir: str
        duckdb_path: str = "/tmp/nrfi.duckdb"
        def resolve_paths(self): return self

    prov = ib.collect_provenance(_Cfg(model_dir=str(tmp_path)))
    assert prov.loaded is False
    assert prov.source == "missing"
    assert prov.model_version == ""
    assert prov.feature_count == 0


def test_collect_provenance_marks_r2_source_when_no_local_files(
    monkeypatch, tmp_path,
):
    """Bundle loaded but local_files dict empty → source='r2'."""
    fake_bridge = _FakeBridge(available=True, bundle=_FakeBundle())
    monkeypatch.setattr(
        "edge_equation.engines.nrfi.integration.engine_bridge."
        "NRFIEngineBridge.try_load",
        classmethod(lambda cls, config=None: fake_bridge),
    )

    @dataclass
    class _Cfg:
        model_dir: str
        duckdb_path: str = "/tmp/nrfi.duckdb"
        def resolve_paths(self): return self

    # No files written to tmp_path — provenance should mark source='r2'
    # because available=True but local_files is empty.
    prov = ib.collect_provenance(_Cfg(model_dir=str(tmp_path)))
    assert prov.loaded is True
    assert prov.local_files == {}
    assert prov.source == "r2"


# ---------------------------------------------------------------------------
# build_report — orchestration + notes
# ---------------------------------------------------------------------------


def test_build_report_adds_note_when_bundle_missing(monkeypatch):
    monkeypatch.setattr(
        ib, "collect_provenance",
        lambda cfg: ib.BundleProvenance(loaded=False, source="missing"),
    )
    monkeypatch.setattr(ib, "_sanity_summary", lambda cfg, season: (None, None))
    monkeypatch.setattr(ib, "_tier_histogram_for_today",
                          lambda cfg: ([], None, 0))

    report = ib.build_report(season=2026)
    assert any("Bundle did not load" in n for n in report.notes)
    assert any("No predictions" in n for n in report.notes)


def test_build_report_includes_sanity_and_histogram(monkeypatch):
    monkeypatch.setattr(
        ib, "collect_provenance",
        lambda cfg: ib.BundleProvenance(
            loaded=True, source="local", model_version="nrfi_v3",
            feature_count=42, model_dir="/tmp/m",
        ),
    )
    monkeypatch.setattr(
        ib, "_sanity_summary",
        lambda cfg, season: ("ml beats baseline by 0.0166 brier", True),
    )
    monkeypatch.setattr(
        ib, "_tier_histogram_for_today",
        lambda cfg: (
            [
                ib.TierHistogramRow(tier="LOCK", band="≥70%", count=2),
                ib.TierHistogramRow(tier="STRONG", band="64-69%", count=4),
            ],
            ("2026-04-29", "2026-04-29"),
            12,
        ),
    )

    report = ib.build_report(season=2026)
    assert report.provenance.loaded is True
    assert report.sanity_passed is True
    assert "ml beats baseline" in report.sanity_summary
    assert report.n_predictions_in_window == 12
    assert any(r.tier == "LOCK" and r.count == 2 for r in report.tier_histogram)
    assert report.notes == []


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


def test_render_report_contains_all_sections():
    report = ib.InspectReport(
        provenance=ib.BundleProvenance(
            loaded=True, source="local", model_version="nrfi_v3",
            feature_count=42, model_dir="/tmp/m",
            local_files={"a.pkl": "2026-04-29T00:00:00+00:00"},
            r2_last_modified="2026-04-28T06:30:00+00:00",
        ),
        sanity_summary="brier-delta -0.0101 vs baseline",
        sanity_passed=True,
        tier_histogram=[
            ib.TierHistogramRow(tier="LOCK", band="≥70%", count=2),
            ib.TierHistogramRow(tier="STRONG", band="64-69%", count=5),
        ],
        probability_window=("2026-04-29", "2026-04-29"),
        n_predictions_in_window=15,
        notes=["foo"],
    )
    text = ib.render_report(report)
    assert "PROVENANCE" in text
    assert "nrfi_v3" in text
    assert "R2 last-modified" in text
    assert "SANITY" in text
    assert "PASS" in text
    assert "TIER HISTOGRAM" in text
    assert "LOCK" in text
    assert "NOTES" in text
    assert "foo" in text


def test_render_report_skips_optional_sections_when_absent():
    report = ib.InspectReport(
        provenance=ib.BundleProvenance(loaded=False, source="missing"),
    )
    text = ib.render_report(report)
    assert "PROVENANCE" in text
    assert "SANITY" not in text
    assert "TIER HISTOGRAM" not in text
    # No notes were added in this construction path, so NOTES section omitted.
    assert "NOTES" not in text


# ---------------------------------------------------------------------------
# CLI exit code
# ---------------------------------------------------------------------------


def test_main_returns_zero_when_bundle_loaded(monkeypatch, capsys):
    monkeypatch.setattr(
        ib, "build_report",
        lambda **kw: ib.InspectReport(
            provenance=ib.BundleProvenance(loaded=True, source="local"),
        ),
    )
    rc = ib.main([])
    assert rc == 0
    assert "PROVENANCE" in capsys.readouterr().out


def test_main_returns_one_when_bundle_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        ib, "build_report",
        lambda **kw: ib.InspectReport(
            provenance=ib.BundleProvenance(loaded=False, source="missing"),
        ),
    )
    rc = ib.main([])
    assert rc == 1


def test_main_emits_valid_json_with_flag(monkeypatch, capsys):
    monkeypatch.setattr(
        ib, "build_report",
        lambda **kw: ib.InspectReport(
            provenance=ib.BundleProvenance(loaded=True, source="local"),
        ),
    )
    rc = ib.main(["--json"])
    assert rc == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert "provenance" in payload
    # feature_names must be stripped from the JSON to keep it tidy.
    assert "feature_names" not in payload["provenance"]

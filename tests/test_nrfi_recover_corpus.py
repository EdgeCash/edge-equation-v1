def test_recover_corpus_downloads_first_available_r2_prefix(tmp_path, monkeypatch):
    from edge_equation.engines.nrfi.data import recover_corpus as mod

    class _Client:
        def list_keys(self, prefix):
            return ["nrfi/corpus/nrfi.duckdb"] if prefix == "nrfi/corpus/" else []

        def download_file(self, key, local_path):
            local_path.write_text("db")

    monkeypatch.setattr(mod.R2Client, "from_env", classmethod(lambda cls: _Client()))

    report = mod.recover_corpus(destination=tmp_path, prefixes=("missing/", "nrfi/corpus/"))

    assert report.source == "r2"
    assert report.downloaded_keys == ["nrfi/corpus/nrfi.duckdb"]
    assert (tmp_path / "nrfi.duckdb").read_text() == "db"


def test_recover_corpus_falls_back_to_api_backfill(monkeypatch):
    from edge_equation.engines.nrfi.data import recover_corpus as mod

    calls = []

    class _Report:
        def summary(self):
            return "chunk"

    monkeypatch.setattr(mod.R2Client, "from_env", classmethod(lambda cls: None))
    monkeypatch.setattr(
        mod,
        "backfill_historical_data",
        lambda start, end, **kw: calls.append((start, end, kw)) or _Report(),
    )

    report = mod.recover_corpus(
        fallback_start="2025-04-01",
        fallback_end="2025-04-03",
        chunk_days=2,
    )

    assert report.source == "api_backfill"
    assert [(c[0], c[1]) for c in calls] == [
        ("2025-04-01", "2025-04-02"),
        ("2025-04-03", "2025-04-03"),
    ]

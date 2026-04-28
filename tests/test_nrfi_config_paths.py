"""Regression tests for `nrfi.config` path resolution.

PR #68 (engines/ migration) moved nrfi/config.py from depth 1 to
depth 4 below the repo root. The old hard-coded `parents[1]` started
silently resolving to `src/edge_equation/engines/` and the DuckDB
store + model artifacts were written to a directory the GitHub
Actions upload step never looked at — silently breaking every
walk-forward training attempt for ~24 hours.

This test pins the contract: `NRFIConfig`'s default `cache_dir`,
`duckdb_path`, and `model_dir` must resolve to paths under the actual
repo root (the directory containing `pyproject.toml`), not under any
intermediate package directory.
"""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    cur = Path(__file__).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise AssertionError("could not find repo root; pyproject.toml missing")


def test_default_cache_dir_resolves_under_repo_root():
    from edge_equation.engines.nrfi.config import NRFIConfig
    cfg = NRFIConfig()
    expected = (_repo_root() / "data" / "nrfi_cache").resolve()
    assert Path(cfg.cache_dir).resolve() == expected, (
        f"cache_dir resolved to {Path(cfg.cache_dir).resolve()!s} "
        f"instead of {expected!s} — engine moved or _REPO_ROOT computation broke"
    )


def test_default_duckdb_path_resolves_under_repo_root():
    from edge_equation.engines.nrfi.config import NRFIConfig
    cfg = NRFIConfig()
    expected = (_repo_root() / "data" / "nrfi_cache" / "nrfi.duckdb").resolve()
    assert Path(cfg.duckdb_path).resolve() == expected


def test_default_model_dir_resolves_under_repo_root():
    from edge_equation.engines.nrfi.config import NRFIConfig
    cfg = NRFIConfig()
    expected = (_repo_root() / "data" / "nrfi_models").resolve()
    assert Path(cfg.model_dir).resolve() == expected


def test_paths_do_not_leak_into_engines_subdir():
    """Belt-and-suspenders — make sure the previous bug pattern can't
    re-emerge. None of the default paths should contain `engines/data`
    or `nrfi/data` segments."""
    from edge_equation.engines.nrfi.config import NRFIConfig
    cfg = NRFIConfig()
    for name, p in (
        ("cache_dir",    cfg.cache_dir),
        ("duckdb_path",  cfg.duckdb_path),
        ("model_dir",    cfg.model_dir),
    ):
        s = str(Path(p).resolve())
        assert "engines/data" not in s, f"{name} leaked into engines subdir: {s}"
        assert "engines/nrfi/data" not in s, f"{name} leaked into engines/nrfi subdir: {s}"


def test_repo_root_finder_walks_up():
    """Confirm the helper used by the production module finds the repo
    root the same way this test does."""
    from edge_equation.engines.nrfi.config import _find_repo_root
    cur_file = Path(__file__).resolve()
    found = _find_repo_root(cur_file)
    assert (found / "pyproject.toml").exists()
    assert found == _repo_root()


def test_repo_root_finder_works_from_deeply_nested_path():
    """Hypothetical: if the engine moves to an even deeper directory
    in the future, the finder must keep working."""
    from edge_equation.engines.nrfi.config import _find_repo_root
    deep = _repo_root() / "src" / "edge_equation" / "engines" / "nrfi" / "data" / "park_factors.py"
    if deep.exists():
        assert _find_repo_root(deep) == _repo_root()

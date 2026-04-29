"""Props engine config — paths + tunable knobs.

Mirrors the NRFI config layout so operators reading both engines see
the same structure. Most defaults are deliberately conservative
(short rolling windows, modest expected PA counts) so the engine
ships predictable numbers on day-one and gets dialled in once we have
ledger data to look at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo-root anchored defaults
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    """Same depth-agnostic anchor pattern NRFI uses (Phase 1b lesson)."""
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur.parents[-1]


_REPO_ROOT = _find_repo_root(Path(__file__))
_DEFAULT_CACHE_DIR = _REPO_ROOT / "data" / "props_cache"
_DEFAULT_DB_PATH = _DEFAULT_CACHE_DIR / "props.duckdb"


# ---------------------------------------------------------------------------
# Knob groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionKnobs:
    """Per-player rate construction parameters.

    Two knobs control how much weight per-player rolling rates get
    relative to the league prior:

    * `lookback_days` — how far back the rolling-rate window stretches.
      60 days ≈ 200 PAs for an everyday hitter, enough signal to beat
      the league prior on most batters.
    * `prior_weight_pa` — Bayesian-prior pseudo-count blended with the
      observed PA count. Higher → projection trusts the league prior
      more for low-volume players. 80 is a reasonable baseline for
      rate stats; matches Tango's standard "200 PA stabilizes" rule
      after one playoff month of data.
    """
    lookback_days: int = 60
    prior_weight_pa: float = 80.0
    prior_weight_bf: float = 250.0   # pitchers have higher BF stabilizers

    # Approximate per-game volume (PAs for a starting batter, BFs for a
    # starting pitcher). Replaced with per-player priors in a future PR.
    expected_batter_pa: float = 4.1
    expected_pitcher_bf: float = 22.0


@dataclass(frozen=True)
class APIConfig:
    """The Odds API quotas + retry knobs."""
    requests_per_minute: int = 60
    request_timeout_s: float = 30.0


@dataclass(frozen=True)
class PropsConfig:
    """Root props-engine config."""
    cache_dir: Path = _DEFAULT_CACHE_DIR
    duckdb_path: Path = _DEFAULT_DB_PATH

    projection: ProjectionKnobs = field(default_factory=ProjectionKnobs)
    api: APIConfig = field(default_factory=APIConfig)

    log_level: str = "INFO"

    def resolve_paths(self) -> "PropsConfig":
        """Ensure cache + DuckDB-parent directories exist on disk."""
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        return self


def get_default_config() -> PropsConfig:
    """Return a freshly-resolved default PropsConfig."""
    return PropsConfig().resolve_paths()

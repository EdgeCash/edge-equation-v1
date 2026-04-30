"""NFL engine config — paths + tunable knobs.

Mirrors `engines/nrfi/config.py` and `engines/full_game/config.py`
for shape consistency. Defaults are conservative and operator-tunable
once we have a backtest. Anchors paths on `pyproject.toml`-root via
the same `_find_repo_root` pattern (Phase 1b lesson).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo-root anchored defaults
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur.parents[-1]


_REPO_ROOT = _find_repo_root(Path(__file__))
_DEFAULT_CACHE_DIR = _REPO_ROOT / "data" / "nfl_cache"
_DEFAULT_DB_PATH = _DEFAULT_CACHE_DIR / "nfl.duckdb"


# ---------------------------------------------------------------------------
# Knob groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionKnobs:
    """Per-team / per-player rate construction parameters.

    NFL sample sizes are tight — one game per week — so:

    * `lookback_games` — how many recent games feed the rolling rate
      (default 6 ≈ ~6 weeks). Whole-season averages are noisier than
      a recency-weighted last-6 in the NFL because rosters / health /
      schedule churn week to week.
    * `prior_weight_games` — Bayesian-prior pseudo-count blended with
      the observed game count. A team with 2 games played gets ~75%
      prior weight; by week 12 it's mostly own-rate.
    * `home_field_advantage_pct` — multiplicative bump on home-team
      expected points. Historical NFL HFA is ~2-2.5 points; we
      surface it as a knob (default ~3% on expected points lift) so
      the operator can tune it season-to-season.
    """
    lookback_games: int = 6
    prior_weight_games: float = 6.0
    home_field_advantage_pct: float = 0.03

    # Pace / volume defaults — average plays per game per team. Used
    # by the player-prop projection layer to compute expected per-game
    # volume × per-play rate.
    expected_plays_per_team: float = 64.0
    expected_pass_attempts_per_team: float = 33.0
    expected_rush_attempts_per_team: float = 24.0


@dataclass(frozen=True)
class APIConfig:
    """The Odds API quotas + retry knobs (shared with other engines)."""
    requests_per_minute: int = 60
    request_timeout_s: float = 30.0


@dataclass(frozen=True)
class NFLConfig:
    """Root NFL-engine config."""
    cache_dir: Path = _DEFAULT_CACHE_DIR
    duckdb_path: Path = _DEFAULT_DB_PATH

    projection: ProjectionKnobs = field(default_factory=ProjectionKnobs)
    api: APIConfig = field(default_factory=APIConfig)

    log_level: str = "INFO"

    def resolve_paths(self) -> "NFLConfig":
        """Ensure cache + DuckDB-parent directories exist on disk."""
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        return self


def get_default_config() -> NFLConfig:
    """Return a freshly-resolved default NFLConfig."""
    return NFLConfig().resolve_paths()

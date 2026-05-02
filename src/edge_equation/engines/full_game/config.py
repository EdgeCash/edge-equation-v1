"""Full-game engine config — paths + tunable knobs.

Mirrors `nrfi/config.py` and `props_prizepicks/config.py` so all three
engines share the same shape. Knobs deliberately conservative on
day-one — operator dials them in once ledger data is in hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo-root anchored defaults (Phase 1b lesson — pyproject anchoring).
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur.parents[-1]


_REPO_ROOT = _find_repo_root(Path(__file__))
# Cache dir + DuckDB path use the no-underscore form to match the
# rest of the codebase. Every workflow CLI invocation, the artifact
# upload/download paths, the docstrings in scrapers_etl.py and
# evaluation/sanity.py, and the daily-feed exporter's
# ``--fullgame-duckdb-path`` flag all reference
# ``data/fullgame_cache/fullgame.duckdb``. The default config used
# to point at ``full_game_cache/full_game.duckdb`` (with
# underscores), which meant inline calls from ``email_report``
# silently opened a different (empty) file than the workflow CLI
# wrote to — every backfill landed in one path, every read came
# from another. The diagnostic logs in PR #132 surfaced the path
# divergence directly. Fix: use the canonical no-underscore form.
_DEFAULT_CACHE_DIR = _REPO_ROOT / "data" / "fullgame_cache"
_DEFAULT_DB_PATH = _DEFAULT_CACHE_DIR / "fullgame.duckdb"


# ---------------------------------------------------------------------------
# Knob groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionKnobs:
    """Per-team rate construction parameters.

    Two knobs control how much weight per-team rolling runs-scored /
    runs-allowed rates get relative to the league prior:

    * `lookback_days` — how far back the rolling-rate window stretches.
      45 days ≈ 40 games for a typical team — enough for the rate to
      stabilize past random short-window noise.
    * `prior_weight_games` — Bayesian-prior pseudo-count (in games)
      blended with the observed game count. Higher → projection trusts
      the league prior more for early-season teams.

    `home_field_advantage_pct` — multiplicative bump applied to the
    home team's expected runs and win prob. League-historical HFA in
    MLB is small (~3% on win prob); we surface it as a knob so the
    operator can tune it season-by-season.
    """
    lookback_days: int = 45
    prior_weight_games: float = 12.0
    home_field_advantage_pct: float = 0.03

    # Approximate per-game volume — for a baseball game, the totals
    # market settles on full-game runs (both teams combined), and
    # the F5_Total settles on first-five innings runs.
    # F5 is approximately 5/9 of full game by inning count, and
    # historically averages ~55% of full-game scoring (starters > bullpen).
    f5_share_of_total: float = 0.55


@dataclass(frozen=True)
class APIConfig:
    """The Odds API quotas + retry knobs."""
    requests_per_minute: int = 60
    request_timeout_s: float = 30.0


@dataclass(frozen=True)
class FullGameConfig:
    """Root full-game-engine config."""
    cache_dir: Path = _DEFAULT_CACHE_DIR
    duckdb_path: Path = _DEFAULT_DB_PATH

    projection: ProjectionKnobs = field(default_factory=ProjectionKnobs)
    api: APIConfig = field(default_factory=APIConfig)

    log_level: str = "INFO"

    def resolve_paths(self) -> "FullGameConfig":
        """Ensure cache + DuckDB-parent directories exist on disk."""
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        return self


def get_default_config() -> FullGameConfig:
    """Return a freshly-resolved default FullGameConfig."""
    return FullGameConfig().resolve_paths()

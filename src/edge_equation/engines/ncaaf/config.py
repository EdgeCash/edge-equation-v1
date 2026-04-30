"""NCAAF engine config — paths + tunable knobs.

Mirrors the NFL config but tuned for the college-football reality:

* **More games per team per season** — 12 regular-season games + bowl
  + (sometimes) a conference championship. Still tight per-team
  sample.
* **Larger talent gap** — top-25 vs unranked produces 30+-point spreads
  routinely. Spread variance is wider; need bigger Bayesian prior
  weight to keep early-season projections stable.
* **Conference tiers matter** — SEC vs Power 5 vs G5 vs FCS need
  different priors. Naive league-average gets early-season tune-up
  games very wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur.parents[-1]


_REPO_ROOT = _find_repo_root(Path(__file__))
_DEFAULT_CACHE_DIR = _REPO_ROOT / "data" / "ncaaf_cache"
_DEFAULT_DB_PATH = _DEFAULT_CACHE_DIR / "ncaaf.duckdb"


@dataclass(frozen=True)
class ProjectionKnobs:
    """Per-team rate construction parameters.

    NCAAF is even tighter than NFL early in the season (3-4 games)
    and the talent gap means league-average priors are a poor starting
    point. We blend toward a CONFERENCE-tier prior, not a league-wide
    prior, to keep early projections honest.
    """
    lookback_games: int = 5
    prior_weight_games: float = 8.0   # heavier prior than NFL — more variance
    home_field_advantage_pct: float = 0.04   # ~3 points typical, slightly higher than NFL

    # Conference tiers feed the prior. Real values land in the
    # projection PR; placeholders here document the intent.
    use_conference_tier_prior: bool = True


@dataclass(frozen=True)
class APIConfig:
    """The Odds API quotas + retry knobs."""
    requests_per_minute: int = 60
    request_timeout_s: float = 30.0


@dataclass(frozen=True)
class NCAAFConfig:
    """Root NCAAF-engine config."""
    cache_dir: Path = _DEFAULT_CACHE_DIR
    duckdb_path: Path = _DEFAULT_DB_PATH

    projection: ProjectionKnobs = field(default_factory=ProjectionKnobs)
    api: APIConfig = field(default_factory=APIConfig)

    log_level: str = "INFO"

    def resolve_paths(self) -> "NCAAFConfig":
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        return self


def get_default_config() -> NCAAFConfig:
    return NCAAFConfig().resolve_paths()

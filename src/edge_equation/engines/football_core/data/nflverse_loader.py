"""NFL games + plays loader via nflverse public parquet feeds.

nflverse publishes weekly-updated parquet files at a stable GitHub
release URL. No API key required. Each season has:

* `games_<season>.parquet` — game-level schedule + final scores +
  spread/total snapshots from a public consensus.
* `pbp_<season>.parquet` — full play-by-play (~50k rows / season,
  ~30 MB). Carries EPA, win-prob, success-rate, rusher/passer/receiver
  ids, etc. — the foundation of the per-team rolling rates.

The loader pulls the parquet directly via HTTPS, normalizes column
names to our schema, and returns DataFrames the storage layer can
upsert in one shot.

Best-effort: any HTTP failure surfaces as a `LoaderError` so the
orchestrator can checkpoint it as a failed (date, op) pair and the
next run retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from edge_equation.utils.logging import get_logger

log = get_logger(__name__)


# nflverse release URL pattern. Stable since 2020. Updated weekly
# during the season; final post-season parquet drops by mid-February.
NFLVERSE_GAMES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "schedules/games_{season}.parquet"
)
NFLVERSE_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "pbp/pbp_{season}.parquet"
)


class LoaderError(RuntimeError):
    """Raised when the loader can't return data — caller checkpoints
    the failure and retries on the next run."""


@dataclass(frozen=True)
class NflverseGamesResult:
    season: int
    n_games: int
    df: object  # pandas DataFrame; not typed to keep the import lazy


def fetch_nflverse_games(
    season: int, *,
    http_client=None,
    games_url: Optional[str] = None,
) -> NflverseGamesResult:
    """Pull the season's games parquet from nflverse.

    `http_client` is an optional injected httpx-like client (tests
    pass a fake). When None, we open a short-lived `httpx.Client`.

    Returns a `NflverseGamesResult` carrying the normalized DataFrame.
    """
    url = (games_url or NFLVERSE_GAMES_URL).format(season=season)
    df = _fetch_parquet(url, http_client=http_client)
    df = _normalize_games_df(df, season=season)
    return NflverseGamesResult(season=season, n_games=int(len(df)), df=df)


@dataclass(frozen=True)
class NflversePbpResult:
    season: int
    n_plays: int
    df: object


def fetch_nflverse_pbp(
    season: int, *,
    http_client=None,
    pbp_url: Optional[str] = None,
) -> NflversePbpResult:
    """Pull the season's play-by-play parquet from nflverse.

    Heavy — ~30 MB per season. Cache via the orchestrator's
    chunk-aware checkpointing so we only re-pull when re-running
    a specific season's backfill.
    """
    url = (pbp_url or NFLVERSE_PBP_URL).format(season=season)
    df = _fetch_parquet(url, http_client=http_client)
    df = _normalize_pbp_df(df, season=season)
    return NflversePbpResult(season=season, n_plays=int(len(df)), df=df)


# ---------------------------------------------------------------------------
# HTTP + normalization
# ---------------------------------------------------------------------------


def _fetch_parquet(url: str, *, http_client=None):
    """Fetch a parquet URL into a pandas DataFrame.

    Uses pyarrow under the hood — pandas's read_parquet handles bytes
    directly so we don't have to spool to disk.
    """
    import io
    import pandas as pd

    owns_client = http_client is None
    if owns_client:
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise LoaderError(
                "httpx is required for nflverse fetch — install via "
                "`pip install -e .[nrfi]`",
            ) from e
        http_client = httpx.Client(timeout=120.0, follow_redirects=True)
    try:
        try:
            resp = http_client.get(url)
            resp.raise_for_status()
        except Exception as e:
            raise LoaderError(f"nflverse fetch failed: {e}") from e
        try:
            return pd.read_parquet(io.BytesIO(resp.content))
        except Exception as e:
            raise LoaderError(f"nflverse parquet decode failed: {e}") from e
    finally:
        if owns_client:
            http_client.close()


def _normalize_games_df(df, *, season: int):
    """Map nflverse columns to our `football_games` schema."""
    import pandas as pd
    if df is None or len(df) == 0:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["game_id"] = df.get("game_id", "").astype(str)
    out["sport"] = "NFL"
    out["season"] = int(season)
    out["week"] = df.get("week", 0).astype(int, errors="ignore")
    out["season_type"] = df.get("season_type", "REG").astype(str)
    out["event_date"] = pd.to_datetime(df.get("gameday")).dt.date
    out["kickoff_ts"] = pd.to_datetime(
        df.get("gametime").astype(str) + " " + df.get("gameday").astype(str),
        errors="coerce",
    )
    out["home_team"] = df.get("home_team", "").astype(str)
    out["away_team"] = df.get("away_team", "").astype(str)
    out["home_tricode"] = df.get("home_team", "").astype(str)
    out["away_tricode"] = df.get("away_team", "").astype(str)
    out["venue"] = df.get("stadium", "").astype(str)
    out["venue_code"] = df.get("stadium_id", "").astype(str)
    out["is_dome"] = df.get("roof", "").astype(str).isin(
        ["dome", "closed", "indoors"],
    )
    out["is_neutral_site"] = df.get(
        "location", "Home",
    ).astype(str).str.lower() == "neutral"
    return out


def _normalize_pbp_df(df, *, season: int):
    """Map nflverse PBP columns to our `football_plays` schema. We
    keep only the play-level fields the projection layer needs;
    full nflverse PBP carries 300+ columns we don't use."""
    import pandas as pd
    if df is None or len(df) == 0:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["game_id"] = df.get("game_id", "").astype(str)
    out["play_id"] = df.get("play_id", "").astype(str)
    out["sport"] = "NFL"
    out["quarter"] = df.get("qtr", 0).astype(int, errors="ignore")
    out["seconds_remaining"] = df.get("game_seconds_remaining", 0).astype(
        int, errors="ignore",
    )
    out["down"] = df.get("down", 0).astype(int, errors="ignore")
    out["yards_to_go"] = df.get("ydstogo", 0).astype(int, errors="ignore")
    out["yardline"] = df.get("yardline_100", 0).astype(int, errors="ignore")
    out["play_type"] = df.get("play_type", "").astype(str)
    out["epa"] = df.get("epa", 0.0).astype(float, errors="ignore")
    # `success` may not exist in older parquets; default to False.
    if "success" in df.columns:
        out["success"] = df["success"].astype(bool)
    else:
        out["success"] = False
    out["home_wp"] = df.get("home_wp", 0.5).astype(float, errors="ignore")
    out["rusher_id"] = df.get("rusher_player_id", "").astype(str)
    out["passer_id"] = df.get("passer_player_id", "").astype(str)
    out["receiver_id"] = df.get("receiver_player_id", "").astype(str)
    return out

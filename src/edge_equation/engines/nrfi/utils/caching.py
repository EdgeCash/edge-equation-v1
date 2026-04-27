"""Lightweight caching layer.

Two flavors:

1. `@disk_cache` — pickle-on-disk memoization for any picklable function.
   Keyed by a stable hash of args/kwargs. Honors a TTL.

2. `parquet_cache` — read/write helpers for the larger DataFrame caches
   (Statcast pulls, weather frames). Stored under cache_dir/<namespace>/.

Heavy deps (pyarrow, pandas) are imported lazily so importing the
module never crashes a slim install.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any, Callable, Optional


def _hash_call(func_name: str, args: tuple, kwargs: dict) -> str:
    blob = json.dumps(
        {"f": func_name, "a": [repr(a) for a in args],
         "k": {k: repr(v) for k, v in sorted(kwargs.items())}},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def disk_cache(
    cache_dir: str | os.PathLike,
    *,
    ttl_seconds: Optional[int] = None,
    namespace: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory: memoize function results to ``cache_dir``."""

    base = Path(cache_dir)
    base.mkdir(parents=True, exist_ok=True)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        ns = namespace or func.__module__.replace(".", "_") + "__" + func.__name__
        target = base / ns
        target.mkdir(parents=True, exist_ok=True)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _hash_call(func.__name__, args, kwargs)
            path = target / f"{key}.pkl"
            if path.exists():
                if ttl_seconds is None or (time.time() - path.stat().st_mtime) < ttl_seconds:
                    try:
                        with path.open("rb") as fh:
                            return pickle.load(fh)
                    except Exception:
                        path.unlink(missing_ok=True)
            result = func(*args, **kwargs)
            try:
                with path.open("wb") as fh:
                    pickle.dump(result, fh, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                path.unlink(missing_ok=True)
            return result

        return wrapper

    return decorator


def parquet_path(cache_dir: str | os.PathLike, namespace: str, key: str) -> Path:
    p = Path(cache_dir) / "parquet" / namespace
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{key}.parquet"


def write_parquet(df, cache_dir, namespace: str, key: str) -> Path:
    """Write a DataFrame to the parquet cache. Returns the path."""
    path = parquet_path(cache_dir, namespace, key)
    df.to_parquet(path, index=False)
    return path


def read_parquet(cache_dir, namespace: str, key: str, ttl_seconds: Optional[int] = None):
    """Return cached DataFrame or None if missing/expired."""
    import pandas as pd  # lazy
    path = parquet_path(cache_dir, namespace, key)
    if not path.exists():
        return None
    if ttl_seconds is not None and (time.time() - path.stat().st_mtime) > ttl_seconds:
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None

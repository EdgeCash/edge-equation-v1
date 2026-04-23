"""
That K Report -- account discipline + X credential routing.

Strict isolation between the two brand identities:

  * @EdgeEquation (main account). Existing main-engine X secrets:
        X_API_KEY / X_API_SECRET /
        X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET

  * @ThatK_Guy (K Report account). New secret set, suffix "_KGUY":
        X_API_KEY_KGUY / X_API_SECRET_KGUY /
        X_ACCESS_TOKEN_KGUY / X_ACCESS_TOKEN_SECRET_KGUY

The resolver below guarantees that a k_guy-target build NEVER reads
the main account's credentials, and vice versa.  Any future poster
step has one entry point (resolve_x_credentials) so there's exactly
one line of code that decides which X identity shipped a given
tweet.  That's the whole point of "strict account discipline".

No K-report posting is automated today -- projections and results
on BOTH accounts stay manual per the current brief -- but the
credential plumbing lands now so when we DO wire a poster later
it can't accidentally cross-post.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


class TargetAccount(str, Enum):
    """Two-identity enum. `value` matches what the CLI takes from
    `--target-account` and what the workflow passes via env."""
    MAIN = "main"
    KGUY = "k_guy"


# Env-var prefixes per identity.  Keep the tuple ordering stable --
# the resolver returns credentials in this exact order.
_KGUY_VARS: Tuple[str, str, str, str] = (
    "X_API_KEY_KGUY",
    "X_API_SECRET_KGUY",
    "X_ACCESS_TOKEN_KGUY",
    "X_ACCESS_TOKEN_SECRET_KGUY",
)
_MAIN_VARS: Tuple[str, str, str, str] = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)


@dataclass(frozen=True)
class XCredentials:
    """One identity's X API credential set + the account it's bound
    to.  `missing` lists env-var names that resolved empty so the
    caller can print a clean error without leaking secrets."""
    account: TargetAccount
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str
    missing: Tuple[str, ...] = ()

    def is_complete(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict:
        # Never serialize the secret values.  The metadata slot is
        # intentionally just the account tag + completeness flag so
        # nothing sensitive leaks into an artifact.
        return {
            "account": self.account.value,
            "complete": self.is_complete(),
            "missing": list(self.missing),
        }


def _env_for(account: TargetAccount) -> Tuple[str, str, str, str]:
    if account == TargetAccount.KGUY:
        return _KGUY_VARS
    return _MAIN_VARS


def resolve_x_credentials(
    account: TargetAccount,
    env: Optional[Dict[str, str]] = None,
) -> XCredentials:
    """Resolve an identity's X credentials from env.  `env` is
    injectable for tests so nothing ever monkey-patches os.environ
    at module import time.

    Critically: when account=KGUY, the resolver ONLY reads the
    *_KGUY suffixed variables.  It does NOT fall back to the main
    secret set.  This prevents a missing KGUY secret from silently
    letting the main account credentials ship a K-Report tweet.
    """
    if env is None:
        env = os.environ
    keys = _env_for(account)
    values = tuple(env.get(k, "") for k in keys)
    missing = tuple(k for k, v in zip(keys, values) if not v)
    return XCredentials(
        account=account,
        api_key=values[0],
        api_secret=values[1],
        access_token=values[2],
        access_token_secret=values[3],
        missing=missing,
    )


def assert_account_separation(
    account: TargetAccount,
    env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Return a list of non-fatal WARNING strings when the process
    env has credentials for BOTH accounts present at once.  Two sets
    being simultaneously available is a foot-gun for a future poster
    step (the wrong one could win by accident), so we surface it but
    don't crash.  Unit test consumes this for regression coverage."""
    if env is None:
        env = os.environ
    warnings: List[str] = []
    kguy = resolve_x_credentials(TargetAccount.KGUY, env=env)
    main = resolve_x_credentials(TargetAccount.MAIN, env=env)
    if kguy.is_complete() and main.is_complete():
        other = TargetAccount.MAIN if account == TargetAccount.KGUY else TargetAccount.KGUY
        warnings.append(
            f"Both K-Guy and Main X credentials are present in env. "
            f"target_account={account.value} is in use; treat the "
            f"{other.value} set as dormant until a poster explicitly "
            f"routes it."
        )
    return warnings


def target_header_tag(account: TargetAccount) -> str:
    """Short non-leaking tag the renderers embed in their output so
    artifacts carry an audit trail of which account they were built
    for.  Human-readable, no secret material."""
    if account == TargetAccount.KGUY:
        return "target=@ThatK_Guy"
    return "target=@EdgeEquation"

"""Cloudflare R2 object storage wrapper.

Cloudflare R2 is S3-API compatible, so we just configure boto3's S3
client with R2's endpoint URL and credentials. This module is the
single entry point — every other module that needs to read or write
trained bundles goes through `R2Client`.

Configuration (env vars)
------------------------

* ``R2_ACCESS_KEY_ID``       — token's access key
* ``R2_SECRET_ACCESS_KEY``   — token's secret
* ``R2_ENDPOINT_URL``        — ``https://<account_id>.r2.cloudflarestorage.com``
* ``R2_BUCKET_NAME``         — defaults to ``edge-equation-bundles`` if unset

When any of the required vars are missing, ``R2Client.from_env()``
returns ``None`` so callers can degrade gracefully (the live daily
run keeps working off whatever's already in `cfg.model_dir`).

Bundle naming convention
------------------------

Per the user's Phase 2c decision::

    nrfi/bundles/nrfi_bundle_YYYYMMDD_v1.bundle    # date-stamped archive
    nrfi/bundles/latest.bundle                     # always the newest passing bundle

The walkforward workflow uploads BOTH on a successful sanity gate;
the daily / inference path always reads ``latest.bundle``.
"""

from __future__ import annotations

import os
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BUCKET = "edge-equation-bundles"
NRFI_BUNDLE_PREFIX = "nrfi/bundles/"
NRFI_LATEST_KEY = NRFI_BUNDLE_PREFIX + "latest.bundle"

ENV_R2_ACCESS_KEY_ID = "R2_ACCESS_KEY_ID"
ENV_R2_SECRET_ACCESS_KEY = "R2_SECRET_ACCESS_KEY"
ENV_R2_ENDPOINT_URL = "R2_ENDPOINT_URL"
ENV_R2_BUCKET_NAME = "R2_BUCKET_NAME"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@dataclass
class R2Client:
    """Thin wrapper around boto3's S3 client configured for Cloudflare R2.

    Construct via `from_env()` so credentials come from the standard
    R2_* environment variables. Direct instantiation is intended only
    for tests (where you'd inject a mocked `s3` boto3 client).
    """

    bucket: str
    endpoint_url: str
    _s3: object  # boto3 S3 client; not typed to avoid import-time dep

    # ---- Construction --------------------------------------------------

    @classmethod
    def from_env(cls) -> Optional["R2Client"]:
        """Build from R2_* env vars. Returns None if required vars are
        missing — callers should fall back to local cache / artifacts."""
        access_key = os.environ.get(ENV_R2_ACCESS_KEY_ID)
        secret_key = os.environ.get(ENV_R2_SECRET_ACCESS_KEY)
        endpoint = os.environ.get(ENV_R2_ENDPOINT_URL)
        bucket = os.environ.get(ENV_R2_BUCKET_NAME, DEFAULT_BUCKET)

        missing = [
            name for name, val in (
                (ENV_R2_ACCESS_KEY_ID, access_key),
                (ENV_R2_SECRET_ACCESS_KEY, secret_key),
                (ENV_R2_ENDPOINT_URL, endpoint),
            ) if not val
        ]
        if missing:
            log.info("R2 client not configured (missing env vars: %s)",
                     ", ".join(missing))
            return None

        try:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore
        except ImportError:
            log.warning("boto3 not installed — R2 client unavailable. "
                         "Install via `pip install -e .[nrfi]`.")
            return None

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # R2 uses "auto" region; signing region defaults to us-east-1.
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 4, "mode": "standard"},
            ),
        )
        return cls(bucket=bucket, endpoint_url=endpoint, _s3=s3)

    # ---- Operations ----------------------------------------------------

    def upload_file(self, local_path: str | Path, key: str) -> None:
        """Upload a single file. Overwrites without confirmation."""
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(local_path)
        log.info("R2 upload: %s -> s3://%s/%s (%d bytes)",
                 local_path, self.bucket, key, local_path.stat().st_size)
        self._s3.upload_file(str(local_path), self.bucket, key)

    def download_file(self, key: str, local_path: str | Path) -> None:
        """Download `key` to `local_path`. Creates parent dirs."""
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("R2 download: s3://%s/%s -> %s",
                 self.bucket, key, local_path)
        self._s3.download_file(self.bucket, key, str(local_path))

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def last_modified(self, key: str):
        """Return the LastModified timestamp for `key`, or None when the
        key doesn't exist or the head call fails. Useful for the bundle
        inspection CLI to surface "this bundle was uploaded N days ago"
        without parsing the date out of the filename.
        """
        try:
            resp = self._s3.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            return None
        return resp.get("LastModified")

    def list_keys(self, prefix: str) -> list[str]:
        """List object keys with the given prefix (no pagination since
        bundle counts are O(weeks-of-history), not millions)."""
        resp = self._s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        return [obj["Key"] for obj in resp.get("Contents", []) or []]

    def delete_key(self, key: str) -> None:
        log.info("R2 delete: s3://%s/%s", self.bucket, key)
        self._s3.delete_object(Bucket=self.bucket, Key=key)


# ---------------------------------------------------------------------------
# Bundle pack/unpack helpers
# ---------------------------------------------------------------------------

def pack_model_dir_to_tarball(model_dir: str | Path,
                                tarball_path: str | Path) -> Path:
    """Tar+gzip the contents of `model_dir` into a single bundle file.

    The walkforward training pipeline writes 4-5 .pkl artifacts plus
    a feature-list JSON to `cfg.model_dir`. We pack them into one
    file for atomic upload — a partial upload during a half-finished
    weekly retrain shouldn't leave the daily run reading a stale
    classifier with a fresh calibrator.
    """
    model_dir = Path(model_dir)
    tarball_path = Path(tarball_path)
    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    if not model_dir.exists():
        raise FileNotFoundError(model_dir)
    with tarfile.open(tarball_path, "w:gz") as tf:
        for child in sorted(model_dir.iterdir()):
            tf.add(child, arcname=child.name)
    return tarball_path


def unpack_tarball_to_model_dir(tarball_path: str | Path,
                                  model_dir: str | Path) -> Path:
    """Inverse of `pack_model_dir_to_tarball`. Cleans the destination
    first so a stale subset from a partial prior unpack can't survive."""
    tarball_path = Path(tarball_path)
    model_dir = Path(model_dir)
    if model_dir.exists():
        shutil.rmtree(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball_path, "r:gz") as tf:
        tf.extractall(model_dir)
    return model_dir


# ---------------------------------------------------------------------------
# Convenience: NRFI bundle namespace
# ---------------------------------------------------------------------------

def date_stamped_bundle_key(date_iso: str, version: str = "v1") -> str:
    """Return ``nrfi/bundles/nrfi_bundle_YYYYMMDD_v1.bundle`` for date_iso."""
    yyyymmdd = date_iso.replace("-", "")
    return f"{NRFI_BUNDLE_PREFIX}nrfi_bundle_{yyyymmdd}_{version}.bundle"


def upload_nrfi_bundle(client: R2Client, model_dir: str | Path,
                         date_iso: str) -> tuple[str, str]:
    """Pack `model_dir`, upload it to R2 under both the date-stamped
    key and the `latest.bundle` pointer. Returns (date_key, latest_key).

    Atomic-enough: we upload the date-stamped key first, then upload
    the same tarball as `latest.bundle`. If the second upload fails
    the dated copy is still available for manual recovery.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "bundle.tar.gz"
        pack_model_dir_to_tarball(model_dir, tar_path)
        date_key = date_stamped_bundle_key(date_iso)
        client.upload_file(tar_path, date_key)
        client.upload_file(tar_path, NRFI_LATEST_KEY)
    return date_key, NRFI_LATEST_KEY


def download_latest_nrfi_bundle(client: R2Client,
                                  model_dir: str | Path) -> Optional[Path]:
    """Try to fetch ``latest.bundle`` and unpack it into `model_dir`.

    Returns the model_dir on success, None when the latest pointer
    doesn't exist yet (first-ever sanity gate hasn't passed).
    """
    if not client.exists(NRFI_LATEST_KEY):
        log.info("R2: %s does not exist yet", NRFI_LATEST_KEY)
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "bundle.tar.gz"
        client.download_file(NRFI_LATEST_KEY, tar_path)
        unpack_tarball_to_model_dir(tar_path, model_dir)
    return Path(model_dir)

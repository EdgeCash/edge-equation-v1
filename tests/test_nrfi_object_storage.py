"""Tests for the Cloudflare R2 wrapper (Phase 2c).

Mocks boto3's S3 client so the tests run in the slim CI workflow
without requiring `boto3` (or, more importantly, real R2 credentials).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# from_env preconditions
# ---------------------------------------------------------------------------

def test_from_env_returns_none_when_creds_missing(monkeypatch):
    """Missing any of the three required env vars → None (callers fall
    back to local cache or Poisson baseline)."""
    from edge_equation.utils.object_storage import R2Client
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("R2_BUCKET_NAME", raising=False)
    assert R2Client.from_env() is None


def test_from_env_returns_none_when_partial_creds(monkeypatch):
    """Missing a single required env var still returns None."""
    from edge_equation.utils.object_storage import R2Client
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
    assert R2Client.from_env() is None


def test_from_env_returns_none_when_boto3_missing(monkeypatch):
    """If boto3 isn't installed, return None — not a hard failure."""
    from edge_equation.utils import object_storage
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://x.r2.cloudflarestorage.com")
    # Simulate missing boto3 by intercepting the import.
    import sys
    real_modules = sys.modules.copy()
    sys.modules.pop("boto3", None)
    sys.modules["boto3"] = None  # makes import raise
    try:
        assert object_storage.R2Client.from_env() is None
    finally:
        sys.modules.clear()
        sys.modules.update(real_modules)


def test_from_env_uses_default_bucket_when_unset(monkeypatch):
    """R2_BUCKET_NAME is optional; defaults to `edge-equation-bundles`."""
    pytest.importorskip("boto3")
    from edge_equation.utils.object_storage import R2Client, DEFAULT_BUCKET
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://x.r2.cloudflarestorage.com")
    monkeypatch.delenv("R2_BUCKET_NAME", raising=False)
    client = R2Client.from_env()
    assert client is not None
    assert client.bucket == DEFAULT_BUCKET


# ---------------------------------------------------------------------------
# Operations on a mocked S3 client
# ---------------------------------------------------------------------------

def _fake_client():
    """Return an R2Client wrapping a MagicMock S3."""
    from edge_equation.utils.object_storage import R2Client
    return R2Client(
        bucket="test-bucket",
        endpoint_url="https://test.r2.cloudflarestorage.com",
        _s3=MagicMock(),
    )


def test_upload_file_calls_boto3(tmp_path):
    client = _fake_client()
    src = tmp_path / "bundle.tar.gz"
    src.write_bytes(b"hello bundle")
    client.upload_file(src, "nrfi/bundles/test.bundle")
    client._s3.upload_file.assert_called_once_with(
        str(src), "test-bucket", "nrfi/bundles/test.bundle"
    )


def test_upload_file_raises_when_local_missing(tmp_path):
    client = _fake_client()
    with pytest.raises(FileNotFoundError):
        client.upload_file(tmp_path / "doesnt_exist", "anything.bundle")


def test_download_file_creates_parents(tmp_path):
    client = _fake_client()
    target = tmp_path / "deep" / "nested" / "bundle.tar.gz"
    client.download_file("nrfi/bundles/latest.bundle", target)
    client._s3.download_file.assert_called_once_with(
        "test-bucket", "nrfi/bundles/latest.bundle", str(target)
    )
    assert target.parent.exists()


def test_exists_returns_true_when_head_succeeds():
    client = _fake_client()
    client._s3.head_object.return_value = {}
    assert client.exists("any-key") is True


def test_exists_returns_false_when_head_raises():
    client = _fake_client()
    client._s3.head_object.side_effect = RuntimeError("404")
    assert client.exists("any-key") is False


def test_list_keys_unwraps_contents():
    client = _fake_client()
    client._s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "nrfi/bundles/latest.bundle"},
            {"Key": "nrfi/bundles/nrfi_bundle_20260428_v1.bundle"},
        ],
    }
    keys = client.list_keys("nrfi/bundles/")
    assert keys == [
        "nrfi/bundles/latest.bundle",
        "nrfi/bundles/nrfi_bundle_20260428_v1.bundle",
    ]


def test_list_keys_handles_empty_result():
    client = _fake_client()
    client._s3.list_objects_v2.return_value = {}
    assert client.list_keys("nrfi/bundles/") == []


# ---------------------------------------------------------------------------
# Bundle pack / unpack round-trip
# ---------------------------------------------------------------------------

def test_pack_and_unpack_round_trip(tmp_path):
    from edge_equation.utils.object_storage import (
        pack_model_dir_to_tarball, unpack_tarball_to_model_dir,
    )
    src = tmp_path / "model_dir"
    src.mkdir()
    (src / "classifier.pkl").write_bytes(b"\x80\x04classifier")
    (src / "regressor.pkl").write_bytes(b"\x80\x04regressor")
    (src / "features.json").write_text('["a","b","c"]')

    tar = tmp_path / "bundle.tar.gz"
    pack_model_dir_to_tarball(src, tar)
    assert tar.exists()
    assert tar.stat().st_size > 0

    dst = tmp_path / "unpacked"
    unpack_tarball_to_model_dir(tar, dst)
    assert (dst / "classifier.pkl").read_bytes() == b"\x80\x04classifier"
    assert (dst / "regressor.pkl").read_bytes() == b"\x80\x04regressor"
    assert (dst / "features.json").read_text() == '["a","b","c"]'


def test_unpack_clears_destination_first(tmp_path):
    """Stale files from a previous unpack must not survive into the
    fresh bundle's directory."""
    from edge_equation.utils.object_storage import (
        pack_model_dir_to_tarball, unpack_tarball_to_model_dir,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "fresh.pkl").write_bytes(b"new")
    tar = tmp_path / "bundle.tar.gz"
    pack_model_dir_to_tarball(src, tar)

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale.pkl").write_bytes(b"old")
    unpack_tarball_to_model_dir(tar, dst)
    assert not (dst / "stale.pkl").exists()
    assert (dst / "fresh.pkl").read_bytes() == b"new"


def test_pack_raises_when_model_dir_missing(tmp_path):
    from edge_equation.utils.object_storage import pack_model_dir_to_tarball
    with pytest.raises(FileNotFoundError):
        pack_model_dir_to_tarball(tmp_path / "nope", tmp_path / "out.tar.gz")


# ---------------------------------------------------------------------------
# Bundle namespace conventions
# ---------------------------------------------------------------------------

def test_date_stamped_bundle_key_format():
    from edge_equation.utils.object_storage import (
        date_stamped_bundle_key, NRFI_BUNDLE_PREFIX,
    )
    assert date_stamped_bundle_key("2026-04-28") == \
        "nrfi/bundles/nrfi_bundle_20260428_v1.bundle"
    assert date_stamped_bundle_key("2026-04-28", version="v2") == \
        "nrfi/bundles/nrfi_bundle_20260428_v2.bundle"
    # Prefix matches the constant.
    assert date_stamped_bundle_key("2026-01-01").startswith(NRFI_BUNDLE_PREFIX)


def test_upload_nrfi_bundle_writes_both_keys(tmp_path):
    from edge_equation.utils.object_storage import (
        upload_nrfi_bundle, NRFI_LATEST_KEY,
    )
    src = tmp_path / "model_dir"
    src.mkdir()
    (src / "classifier.pkl").write_bytes(b"\x80\x04test")

    client = _fake_client()
    date_key, latest_key = upload_nrfi_bundle(client, src, "2026-04-28")

    assert date_key == "nrfi/bundles/nrfi_bundle_20260428_v1.bundle"
    assert latest_key == NRFI_LATEST_KEY

    # Two upload calls, same tarball, different keys.
    assert client._s3.upload_file.call_count == 2
    call_keys = [call.args[2] for call in client._s3.upload_file.call_args_list]
    assert date_key in call_keys
    assert latest_key in call_keys


def test_download_latest_returns_none_when_missing(tmp_path):
    from edge_equation.utils.object_storage import download_latest_nrfi_bundle
    client = _fake_client()
    client._s3.head_object.side_effect = RuntimeError("404")
    result = download_latest_nrfi_bundle(client, tmp_path / "model_dir")
    assert result is None
    client._s3.download_file.assert_not_called()

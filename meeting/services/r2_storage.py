"""Cloudflare R2 object storage wrapper.

Uses boto3's S3 client pointed at R2's S3-compatible endpoint
(``https://<account>.r2.cloudflarestorage.com``). All paths Mee used
to write to ``output/audio/`` and ``output/voice_enrollment/`` now go
through this module so they survive container restarts and scale beyond
one disk.

Env vars (from project .env):
  R2_ACCOUNT_ID            – Cloudflare account id (for endpoint URL)
  R2_ACCESS_KEY_ID         – R2 API token access key id
  R2_SECRET_ACCESS_KEY     – matching secret
  R2_BUCKET                – bucket name (must exist in R2 dashboard)
  R2_PUBLIC_BASE_URL       – optional public-domain prefix
                              (e.g. https://media.mee.example.com).
                              When set, get_public_url() returns that
                              instead of a presigned URL — cheaper +
                              cache-friendly. Leave empty to fall back
                              to presigned URLs.

Local-fallback: when any of the *_ID/*_KEY/*_BUCKET vars are missing
the service degrades to disk writes under ``output/`` so dev machines
without R2 still work. ``is_configured()`` lets callers branch.

Keys we use:
  audio/<recording_id>.<ext>          – raw meeting upload
  voiceprints/<user_id>.<ext>         – per-user voice enrollment sample
  speaker-samples/<rec_id>/<spk>.wav  – per-cluster 3s sample for SpeakerMapper

All keys are flat under the bucket root. We never put PII in the path
itself — uuids only, lookup metadata lives in Postgres.
"""
from __future__ import annotations

import logging
import os
from typing import IO, BinaryIO, Optional

try:
    import boto3
    from botocore.client import Config
    from botocore.exceptions import BotoCoreError, ClientError
    BOTO3_AVAILABLE = True
except ImportError:  # boto3 not installed yet (e.g. fresh checkout)
    boto3 = None  # type: ignore
    Config = None  # type: ignore
    BotoCoreError = ClientError = Exception  # type: ignore
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)


def _read_env() -> dict:
    """Read R2 config from env. Centralised so the cached singleton can
    re-read on first call without scattering ``os.getenv`` calls."""
    return {
        "account_id": os.getenv("R2_ACCOUNT_ID", "").strip(),
        "access_key_id": os.getenv("R2_ACCESS_KEY_ID", "").strip(),
        "secret_access_key": os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
        "bucket": os.getenv("R2_BUCKET", "").strip(),
        "public_base_url": os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/"),
    }


def is_configured() -> bool:
    """True when all required R2 vars are present in env. Callers branch
    on this so the codebase can run without R2 in dev."""
    if not BOTO3_AVAILABLE:
        return False
    cfg = _read_env()
    return all(cfg[k] for k in ("account_id", "access_key_id", "secret_access_key", "bucket"))


_client = None


def _get_client():
    """Lazy-init the boto3 S3 client. Reused across calls within a
    process. R2 quirks: signature_version='s3v4' is required, region is
    'auto', and the endpoint URL is per-account."""
    global _client
    if _client is not None:
        return _client
    if not is_configured():
        raise RuntimeError("R2 storage not configured — set R2_* env vars")
    cfg = _read_env()
    _client = boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return _client


def _bucket() -> str:
    return _read_env()["bucket"]


_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
}


def _content_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def upload_bytes(key: str, data: bytes, content_type: Optional[str] = None) -> str:
    """Upload an in-memory blob to R2 and return the full key (echoed
    back so callers can store it on the model). Raises on failure."""
    ct = content_type or _content_type_for(key)
    client = _get_client()
    client.put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=ct)
    logger.info("[R2] uploaded %s (%d bytes, %s)", key, len(data), ct)
    return key


def upload_fileobj(key: str, fileobj: BinaryIO, content_type: Optional[str] = None) -> str:
    """Stream-upload a file-like object to R2. Use this for large audio
    so we don't load the whole thing into memory."""
    ct = content_type or _content_type_for(key)
    client = _get_client()
    client.upload_fileobj(
        fileobj, _bucket(), key,
        ExtraArgs={"ContentType": ct},
    )
    logger.info("[R2] uploaded (stream) %s (%s)", key, ct)
    return key


def download_bytes(key: str) -> bytes:
    """Read an R2 object back into memory. Use sparingly — for small
    voiceprint samples or one-shot pipeline reads. For large audio,
    prefer ``presigned_url`` and let the browser stream directly."""
    client = _get_client()
    resp = client.get_object(Bucket=_bucket(), Key=key)
    return resp["Body"].read()


def object_exists(key: str) -> bool:
    """HEAD check — returns True iff the object exists. Useful when
    healing legacy local-only records that may already have an R2 copy."""
    try:
        client = _get_client()
        client.head_object(Bucket=_bucket(), Key=key)
        return True
    except ClientError as e:
        # Boto's exception code for missing object is "404" or "NoSuchKey".
        code = (e.response or {}).get("Error", {}).get("Code", "")
        if str(code) in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def delete_object(key: str) -> None:
    """Remove an R2 object. Idempotent — missing key is not an error."""
    try:
        _get_client().delete_object(Bucket=_bucket(), Key=key)
        logger.info("[R2] deleted %s", key)
    except (BotoCoreError, ClientError) as e:
        logger.warning("[R2] delete %s failed: %s", key, e)


def presigned_url(key: str, expires_sec: int = 3600) -> str:
    """Generate a short-lived URL the browser can GET directly. Default
    1h is plenty for an audio player session; bump for longer demos."""
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires_sec,
    )


def public_or_presigned_url(key: str, expires_sec: int = 3600) -> str:
    """Prefer a stable public-domain URL when R2_PUBLIC_BASE_URL is set
    (cheaper, cacheable). Falls back to presigned otherwise."""
    base = _read_env()["public_base_url"]
    if base:
        return f"{base}/{key.lstrip('/')}"
    return presigned_url(key, expires_sec=expires_sec)


# ── Convenience key builders ──────────────────────────────────────

def audio_key(recording_id: str, ext: str) -> str:
    """Canonical key for a recording's source audio file."""
    if not ext.startswith("."):
        ext = "." + ext
    return f"audio/{recording_id}{ext.lower()}"


def voiceprint_key(user_id: str, ext: str = ".wav") -> str:
    """Canonical key for a user's enrollment recording."""
    if not ext.startswith("."):
        ext = "." + ext
    return f"voiceprints/{user_id}{ext.lower()}"


def speaker_sample_key(recording_id: str, cluster_label: str) -> str:
    """Per-cluster 3s sample played in the SpeakerMapper preview button."""
    safe = cluster_label.replace("/", "_")
    return f"speaker-samples/{recording_id}/{safe}.wav"

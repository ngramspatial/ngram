"""Attachment blob storage: local disk (default) vs S3-compatible HTTP API."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from ngram.config import EntityConfig


def _expand(path: str, entity_name: str) -> Path:
    return Path(path.replace("{entity_name}", entity_name)).expanduser()


class LocalDiskAttachmentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    async def put(self, key: str, data: bytes, content_type: str | None) -> str:
        safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        p = self.root / f"{safe}_{int(time.time() * 1000)}.bin"
        p.write_bytes(data)
        return str(p)

    async def get(self, key: str) -> bytes | None:
        p = Path(key)
        if not p.is_file():
            return None
        return p.read_bytes()


class ObjectStorageAttachmentStore:
    """Minimal S3-compatible signer (AWS signature v4) for PutObject/GetObject."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        prefix: str,
        region: str = "us-east-1",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.prefix = prefix.strip("/")
        self.region = region

    def _object_key(self, key: str) -> str:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return f"{self.prefix}/{h}" if self.prefix else h

    async def put(self, key: str, data: bytes, content_type: str | None) -> str:
        ct = content_type or "application/octet-stream"
        ok = self._object_key(key)
        url = f"{self.endpoint}/{self.bucket}/{ok}"
        # Presigned-style: use unsigned payload for small files via aiohttp + AWS4-HMAC-SHA256
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        host = url.split("://", 1)[-1].split("/", 1)[0]
        canonical_uri = f"/{self.bucket}/{ok}"
        payload_hash = hashlib.sha256(data).hexdigest()
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        canonical_request = (
            f"PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _sign(k: bytes, msg: str) -> bytes:
            return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _sign(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        k_region = _sign(k_date, self.region)
        k_service = _sign(k_region, "s3")
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        authorization = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers = {
            "Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization,
            "Content-Type": ct,
        }
        async with aiohttp.ClientSession() as session:
            async with session.put(url, data=data, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"S3 put failed {resp.status}: {text[:200]}")
        return f"s3://{self.bucket}/{ok}"

    async def get(self, key: str) -> bytes | None:
        if not key.startswith("s3://"):
            return None
        rest = key[5:]
        bucket, _, obj_key = rest.partition("/")
        if bucket != self.bucket:
            return None
        url = f"{self.endpoint}/{bucket}/{obj_key}"
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        host = url.split("://", 1)[-1].split("/", 1)[0]
        canonical_uri = f"/{bucket}/{obj_key}"
        payload_hash = hashlib.sha256(b"").hexdigest()
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        canonical_request = (
            f"GET\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _sign(k: bytes, msg: str) -> bytes:
            return hmac.new(k, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _sign(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        k_region = _sign(k_date, self.region)
        k_service = _sign(k_region, "s3")
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        authorization = (
            f"{algorithm} Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers = {
            "Host": host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    return None
                return await resp.read()


def build_attachment_store(entity: EntityConfig) -> LocalDiskAttachmentStore | ObjectStorageAttachmentStore:
    live_cache = str(getattr(entity, "runtime_cache_dir", "") or "").strip()
    if live_cache:
        return LocalDiskAttachmentStore(Path(live_cache).expanduser() / "attachments")
    h = entity.harness.attachments
    mode = (h.backend or "local_disk").strip().lower()
    if mode == "object_s3_compat":
        ep = (os.environ.get(h.endpoint_url_env) or "").strip().rstrip("/")
        bucket = (os.environ.get(h.bucket_env) or "").strip()
        ak = (os.environ.get(h.access_key_env) or "").strip()
        sk = (os.environ.get(h.secret_key_env) or "").strip()
        if not all((ep, bucket, ak, sk)):
            raise RuntimeError(
                "object_s3_compat requires endpoint, bucket, access key, and secret in env "
                f"({h.endpoint_url_env}, {h.bucket_env}, ...)"
            )
        return ObjectStorageAttachmentStore(
            endpoint=ep,
            bucket=bucket,
            access_key=ak,
            secret_key=sk,
            prefix=h.prefix or "ngram",
        )
    root = _expand(h.local_dir, entity.name)
    return LocalDiskAttachmentStore(root)

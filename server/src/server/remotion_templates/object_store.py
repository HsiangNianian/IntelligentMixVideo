"""Durable Remotion files (sealed versions, reference images, Sprite preview bundles) behind one object interface.

With ZOS configured, objects are uploaded public-read (`zos.upload_public`, which verifies size and anonymous
reads) under `imv/remotion/`; while `ZOS_BUCKET` is empty, the same keys are files beneath the data folder, so local
development, the bundled desktop backend and tests need no cloud credentials. A bucket with incomplete settings is
an error rather than a silent local fallback. The backend reads objects back through a local cache that callers
verify against the SHA-256 recorded in MySQL; the cache is disposable and never the source of truth.
"""

import logging
import shutil
from contextlib import closing
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from ..video_composition.settings import ZosSettings
from ..video_composition.zos import storage_client, upload_public

PREFIX = "imv/remotion"
TIMEOUT = 60.0
logger = logging.getLogger(__name__)


class ObjectStoreError(OSError):
    """The object store could not be reached or refused a request; routes answer 503 so the caller can retry."""


class Objects(Protocol):
    """Write once, read back, and delete whole prefixes; keys are server-generated paths."""

    def put(self, key: str, path: Path, content_type: str) -> None:
        """Store the file's bytes under the key, replacing any earlier object."""

    def fetch(self, key: str, destination: Path) -> None:
        """Copy the object into a local file; raise FileNotFoundError when it does not exist."""

    def delete_prefix(self, prefix: str) -> None:
        """Remove every object whose key starts with the prefix; missing objects are fine."""


class LocalObjects:
    """Objects as files below one directory, written through a temporary sibling and an atomic rename."""

    def __init__(self, root: Path) -> None:
        """Remember the directory; it is created on the first write."""
        self.root = root

    def _path(self, key: str) -> Path:
        """Map a key to a file and refuse keys that would leave the directory."""
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("object key leaves the storage directory")
        return path

    def put(self, key: str, path: Path, content_type: str) -> None:
        """Copy beside the target first so readers never see a partial object."""
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(path, staged)
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)

    def fetch(self, key: str, destination: Path) -> None:
        """Copy the stored file; a missing object raises FileNotFoundError from the copy."""
        shutil.copyfile(self._path(key), destination)

    def delete_prefix(self, prefix: str) -> None:
        """Remove a stored file or a whole directory of objects."""
        path = self._path(prefix.rstrip("/"))
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


class ZosObjects:
    """Objects in the configured ZOS bucket; uploads are public-read and checked by `upload_public`."""

    def __init__(self, settings: ZosSettings) -> None:
        """Keep the validated settings; each call opens and closes its own client."""
        self.settings = settings

    def put(self, key: str, path: Path, content_type: str) -> None:
        """Upload public-read and confirm size and anonymous read before returning; failures are ObjectStoreError."""
        try:
            upload_public([(path, key, content_type)], self.settings, TIMEOUT)
        except (BotoCoreError, ClientError, httpx.HTTPError, ValueError) as exc:
            raise ObjectStoreError(f"ZOS upload failed: {key}") from exc

    def fetch(self, key: str, destination: Path) -> None:
        """Download with the server credentials; a missing key becomes FileNotFoundError, other failures ObjectStoreError."""
        try:
            with closing(storage_client(self.settings, TIMEOUT)) as storage:
                storage.download_file(self.settings.zos_bucket, key, str(destination))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                raise FileNotFoundError(key) from exc
            raise ObjectStoreError(f"ZOS download failed: {key}") from exc
        except BotoCoreError as exc:
            raise ObjectStoreError(f"ZOS download failed: {key}") from exc

    def delete_prefix(self, prefix: str) -> None:
        """List the prefix page by page and delete each page in one request; failures are ObjectStoreError.

        A quiet batch delete still answers 200 when single keys fail (for example AccessDenied); those keys are
        listed under `Errors` and fail the call too, so the caller keeps its records and can retry.
        """
        try:
            with closing(storage_client(self.settings, TIMEOUT)) as storage:
                pages = storage.get_paginator("list_objects_v2").paginate(Bucket=self.settings.zos_bucket, Prefix=prefix)
                for page in pages:
                    keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                    if keys:
                        result = storage.delete_objects(Bucket=self.settings.zos_bucket, Delete={"Objects": keys, "Quiet": True})
                        if errors := result.get("Errors"):
                            codes = sorted({str(error.get("Code")) for error in errors})
                            raise ObjectStoreError(f"ZOS delete failed: {prefix} ({len(errors)} keys, {', '.join(codes)})")
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStoreError(f"ZOS delete failed: {prefix}") from exc


def load_objects(data_dir: Path) -> Objects:
    """Use ZOS once `ZOS_BUCKET` is set, the data folder while it is empty; a bucket with invalid settings is an error.

    `.env.example` ships a default endpoint with an empty bucket, so only the bucket decides whether ZOS is wanted.
    """
    try:
        return ZosObjects(ZosSettings())
    except ValidationError as exc:
        if not any(error["loc"] == ("zos_bucket",) and error["type"] in {"missing", "string_too_short"} for error in exc.errors()):
            raise ValueError("ZOS 配置不完整或无效，请检查 server/.env") from None
    logger.info("ZOS is not configured; Remotion files are stored in %s", data_dir / "objects")
    return LocalObjects(data_dir / "objects")

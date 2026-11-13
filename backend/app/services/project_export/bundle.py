"""
The export archive: files in a staging directory, each recorded with its
checksum, then ``manifest.json`` and one ``.tar.gz`` of the lot.

Manifest schema (``cnp.project-export/v1``)::

    {
      "schema": "cnp.project-export/v1",
      "export_id": "...", "project": "...", "created_at": "<ISO 8601 UTC>",
      "items": [
        {"path": "cmp/deployments.jsonl", "type": "cmp.table",
         "source": "cmp-db:deployments", "format": "application/x-ndjson",
         "sha256": "<hex>", "size": 123, "metadata": {...}}
      ],
      "not_exported": [{"data": "...", "owner": "...", "reason": "..."}],
      "problems": [{"source": "...", "error": "..."}]
    }

Every file of the archive except ``manifest.json`` is an item, and every item
is a file of the archive: the restore drill checks both directions.
"""

import hashlib
import json
import tarfile
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "cnp.project-export/v1"
MANIFEST = "manifest.json"

JSONL = "application/x-ndjson"
JSON = "application/json"
SQL = "application/sql"
GIT_BUNDLE = "application/x-git-bundle"
OCTET = "application/octet-stream"


class UnsafePathError(ValueError):
    """A member path that would land outside the archive root."""


def jsonable(value: Any) -> Any:
    """A JSON-serialisable form of a database value."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(rel: str) -> str:
    """
    *rel* as a clean relative POSIX path. Object keys and resource names come
    from the project, so a ``..`` or an absolute path is refused rather than
    normalised.
    """
    path = PurePosixPath(rel)
    if (
        not rel
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in rel.split("/"))
        or "\\" in rel
        or "\x00" in rel
    ):
        raise UnsafePathError(f"unsafe path in archive: {rel!r}")
    return str(path)


class Bundle:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[dict] = []
        self.problems: list[dict] = []

    def path(self, rel: str) -> Path:
        """Where to write *rel* before calling :meth:`add_file`."""
        target = self.root / safe_relative(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def add_file(
        self,
        rel: str,
        *,
        item_type: str,
        source: str,
        media_type: str,
        metadata: dict | None = None,
    ) -> dict:
        target = self.root / safe_relative(rel)
        item = {
            "path": safe_relative(rel),
            "type": item_type,
            "source": source,
            "format": media_type,
            "sha256": sha256_file(target),
            "size": target.stat().st_size,
        }
        if metadata:
            item["metadata"] = metadata
        self.items.append(item)
        return item

    def add_bytes(self, rel: str, data: bytes, **kwargs: Any) -> dict:
        self.path(rel).write_bytes(data)
        return self.add_file(rel, **kwargs)

    def add_json(self, rel: str, value: Any, **kwargs: Any) -> dict:
        data = json.dumps(value, indent=2, sort_keys=True, default=jsonable)
        return self.add_bytes(
            rel, data.encode() + b"\n", media_type=JSON, **kwargs
        )

    def add_jsonl(self, rel: str, rows: Iterable[dict], **kwargs: Any) -> dict:
        count = 0
        with self.path(rel).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, sort_keys=True, default=jsonable) + "\n"
                )
                count += 1
        metadata = {**kwargs.pop("metadata", {}), "rows": count}
        return self.add_file(
            rel, media_type=JSONL, metadata=metadata, **kwargs
        )

    def problem(self, source: str, error: str) -> None:
        self.problems.append({"source": source, "error": error})

    def write_manifest(self, header: dict, not_exported: list[dict]) -> None:
        manifest = {
            "schema": SCHEMA,
            **header,
            "items": sorted(self.items, key=lambda item: item["path"]),
            "not_exported": not_exported,
            "problems": self.problems,
        }
        (self.root / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def archive(self, dest: Path) -> tuple[str, int]:
        """Write the ``.tar.gz``; returns its sha256 and size."""
        members = [MANIFEST] + sorted(item["path"] for item in self.items)
        with tarfile.open(dest, "w:gz") as tar:
            for member in members:
                tar.add(self.root / member, arcname=member, recursive=False)
        return sha256_file(dest), dest.stat().st_size

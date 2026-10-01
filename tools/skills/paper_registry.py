# -*- coding: utf-8 -*-
"""Content-addressed identity registry for imported and generated papers.

The exam pipeline historically linked files through timestamps and
``EXAM_PAPER_ID`` comments.  This registry adds a stable source identity
without changing that legacy identifier.  Records are kept in a small JSONL
file under ``.memory`` and are rewritten atomically on upsert.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Dict, Iterable, List, Optional
import unicodedata

try:
    from ky_io import atomic_write_text
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text

try:
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root


_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def normalize_content(content: Any) -> str:
    """Normalize source text before hashing, preserving meaningful content."""
    text = unicodedata.normalize("NFKC", str(content or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def paper_id_for_content(content: Any) -> str:
    """Return the stable content-addressed paper id."""
    digest = hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()
    return f"PAPER-{digest[:16]}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _string(value: Any) -> str:
    return str(value) if value is not None else ""


class PaperRegistry:
    """Small, atomic, idempotent paper metadata store."""

    def __init__(self, workspace_root: Optional[Path] = None,
                 registry_path: Optional[Path] = None):
        root = Path(workspace_root) if workspace_root else resolve_workspace_root(__file__)
        self.workspace_root = root.resolve()
        self.path = Path(registry_path) if registry_path else self.workspace_root / ".memory" / "papers.jsonl"
        self.path = self.path.resolve()

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    def register_paper(
        self,
        content: Any,
        *,
        subject: str = "",
        source_name: str = "",
        source_path: Any = "",
        card_path: Any = "",
        key_path: Any = "",
        metadata: Optional[Dict[str, Any]] = None,
        paper_id: str = "",
    ) -> Dict[str, Any]:
        """Insert or update one record and return the complete record.

        Re-registering identical content merges non-empty paths and metadata,
        so an ingest followed by exam composition does not create duplicates.
        """
        normalized = normalize_content(content)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        pid = _string(paper_id).strip() or f"PAPER-{digest[:16]}"
        supplied = metadata if isinstance(metadata, dict) else {}
        with self._locked():
            records = self._read_records()
            current = records.get(pid, {})
            merged_meta = dict(current.get("metadata") or {})
            merged_meta.update(supplied)
            record = {
                "paper_id": pid,
                "subject": _string(subject).strip() or _string(current.get("subject")),
                "source_name": _string(source_name).strip() or _string(current.get("source_name")),
                "source_path": _string(source_path).strip() or _string(current.get("source_path")),
                "card_path": _string(card_path).strip() or _string(current.get("card_path")),
                "key_path": _string(key_path).strip() or _string(current.get("key_path")),
                "created_at": _string(current.get("created_at")) or _now(),
                "updated_at": _now(),
                "content_sha256": digest,
                "metadata": merged_meta,
            }
            records[pid] = record
            self._write_records(records.values())
            return dict(record)

    def get_paper(self, paper_id: str) -> Optional[Dict[str, Any]]:
        pid = _string(paper_id).strip()
        if not pid:
            return None
        with self._locked():
            record = self._read_records().get(pid)
        return dict(record) if record else None

    def list_papers(self, subject: str = "") -> List[Dict[str, Any]]:
        wanted = _string(subject).strip().lower()
        with self._locked():
            records = list(self._read_records().values())
        if wanted:
            records = [r for r in records if _string(r.get("subject")).lower() == wanted]
        return sorted((dict(r) for r in records), key=lambda r: (
            _string(r.get("created_at")), _string(r.get("paper_id"))))

    def find_by_content(self, content: Any) -> Optional[Dict[str, Any]]:
        return self.get_paper(paper_id_for_content(content))

    def find_by_metadata(self, key: str, value: Any) -> List[Dict[str, Any]]:
        wanted = _string(value)
        with self._locked():
            records = list(self._read_records().values())
        result = []
        for record in records:
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            if _string(metadata.get(key)) == wanted:
                result.append(dict(record))
        return result

    def _read_records(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        records: Dict[str, Dict[str, Any]] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return records
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("paper_id"):
                records[str(item["paper_id"])] = item
        return records

    def _write_records(self, records: Iterable[Dict[str, Any]]) -> None:
        ordered = sorted(records, key=lambda r: _string(r.get("paper_id")))
        payload = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ordered)
        atomic_write_text(self.path, payload, sensitive=False)

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path).casefold()
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.RLock())
        # ky_io's atomic writer already uses a cross-process lock for the final
        # file.  The process lock closes the read/merge/write race in this store;
        # filelock is optional and is used when available for the full section.
        try:
            from filelock import FileLock
            return _CombinedLock(lock, FileLock(str(self.lock_path), timeout=10))
        except ImportError:
            return lock


class _CombinedLock:
    def __init__(self, thread_lock: threading.RLock, file_lock: Any):
        self.thread_lock = thread_lock
        self.file_lock = file_lock

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.file_lock.acquire()
        except Exception:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.file_lock.release()
        finally:
            self.thread_lock.release()


def register_paper(content: Any, workspace_root: Optional[Path] = None, **kwargs: Any) -> Dict[str, Any]:
    return PaperRegistry(workspace_root).register_paper(content, **kwargs)


def get_paper(paper_id: str, workspace_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return PaperRegistry(workspace_root).get_paper(paper_id)


def list_papers(workspace_root: Optional[Path] = None, subject: str = "") -> List[Dict[str, Any]]:
    return PaperRegistry(workspace_root).list_papers(subject=subject)


def find_by_content(content: Any, workspace_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return PaperRegistry(workspace_root).find_by_content(content)


__all__ = [
    "PaperRegistry",
    "find_by_content",
    "get_paper",
    "list_papers",
    "normalize_content",
    "paper_id_for_content",
    "register_paper",
]

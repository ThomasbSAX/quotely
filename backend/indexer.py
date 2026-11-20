"""
Store lifecycle and bulk indexing with crash-safe checkpoints.

Bulk jobs save the store every SAVE_EVERY_FILES files, so an interruption loses at most that
many files of work; re-running the job skips everything already stored (content hash per path).
A full reindex builds a separate staging store and swaps it in only when complete, so the live
index stays usable during the rebuild and an interrupted rebuild resumes where it stopped.

F files, N stored rows: one pass is O(F) hash checks + Σ ingest cost + O(F / SAVE_EVERY · N·d) I/O.
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Iterable

import config
from ingest import file_hash, ingest_file, is_supported, remove_file
from vector_store import VectorStore

_store: VectorStore | None = None
_store_lock = threading.Lock()
_job_lock = threading.Lock()
progress = {"running": False, "job": "", "done": 0, "total": 0, "errors": []}


def store() -> VectorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VectorStore(config.INDEX_PATH, config.INDEX_SIGNATURE, config.EMBED_DIM)
    return _store


def scan(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*") if p.is_file() and is_supported(p))


def index_paths(paths: Iterable[Path], target: VectorStore, job: str) -> dict:
    paths = list(paths)
    indexed = skipped = 0
    errors: list[str] = []
    progress.update(running=True, job=job, done=0, total=len(paths), errors=errors)
    try:
        for n, p in enumerate(paths, 1):
            try:
                if ingest_file(p, target, file_hash(p)) is None:
                    skipped += 1
                else:
                    indexed += 1
            except Exception as e:
                errors.append(f"{p.name}: {e}")
            progress["done"] = n
            if n % config.SAVE_EVERY_FILES == 0:
                target.save()
    finally:
        target.save()
        progress["running"] = False
    return {"indexed": indexed, "skipped": skipped, "errors": errors}


def index_folder(folder: Path) -> dict:
    with _job_lock:
        return index_paths(scan(folder), store(), f"folder:{folder}")


def reindex() -> dict:
    global _store
    with _job_lock:
        live = store()
        files = {Path(p) for p in live.file_paths() + live.stale_paths if Path(p).is_file()}
        files.update(scan(config.DATA_PATH))
        staging = VectorStore(config.STAGING_PATH, config.INDEX_SIGNATURE, config.EMBED_DIM)
        result = index_paths(sorted(files), staging, "reindex")
        with _store_lock, live.lock:
            shutil.rmtree(config.INDEX_PATH, ignore_errors=True)
            config.STAGING_PATH.rename(config.INDEX_PATH)
            staging.path = config.INDEX_PATH
            _store = staging
        config.LEGACY_PICKLE.unlink(missing_ok=True)
        return {"total": result["indexed"] + result["skipped"], "errors": result["errors"]}


def startup() -> None:
    live = store()
    if config.STAGING_PATH.exists() or live.stale_paths:
        reindex()
        return
    with _job_lock:
        gone = [p for p in live.file_paths() if not Path(p).exists()]
        for p in gone:
            remove_file(Path(p), live)
        index_paths(scan(config.DATA_PATH), live, "startup")


def on_changed(path: Path) -> None:
    with _job_lock:
        if is_supported(path) and path.is_file() and ingest_file(path, store()):
            store().save()


def on_deleted(path: Path) -> None:
    with _job_lock:
        if remove_file(path, store()):
            store().save()

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
import embeddings as embed
from bibtex import BibLibrary
from ingest import file_hash, ingest_file, is_supported, remove_file
from vector_store import VectorStore

_store: VectorStore | None = None
_store_lock = threading.Lock()
_job_lock = threading.Lock()
progress = {"running": False, "job": "", "done": 0, "total": 0, "errors": []}
library = BibLibrary()
_bib_loaded: set[Path] = set()


def load_bibs(folders: Iterable[Path]) -> None:
    for folder in {Path(f) for f in folders}:
        if not folder.is_dir():
            continue
        for bib in folder.glob("*.bib") if folder != config.DATA_PATH else folder.rglob("*.bib"):
            if bib not in _bib_loaded and not bib.name.startswith("."):
                library.load(bib)
                _bib_loaded.add(bib)


def store() -> VectorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VectorStore(config.INDEX_PATH, signature(), config.EMBED_DIM)
    return _store


def signature() -> str:
    return f"{embed.model_id()}|{config.INDEX_VERSION}"


def ready() -> VectorStore | None:
    return _store


def scan(folder: Path) -> list[Path]:
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file() and is_supported(p) and not any(part.startswith(".") for part in p.parts))
    load_bibs({folder, *(p.parent for p in files)})
    return files


def index_paths(paths: Iterable[Path], target: VectorStore, job: str) -> dict:
    paths = list(paths)
    indexed = skipped = 0
    errors: list[str] = []
    progress.update(running=True, job=job, done=0, total=len(paths), errors=errors)
    try:
        for n, p in enumerate(paths, 1):
            try:
                if ingest_file(p, target, file_hash(p), library) is None:
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
        files = {Path(p) for p in list(live.docs) + live.stale_paths if Path(p).is_file()}
        files.update(scan(config.DATA_PATH))
        staging = VectorStore(config.STAGING_PATH, signature(), config.EMBED_DIM)
        staging.stale_keys = {**live.stale_keys, **{p: d["bibtex_key"] for p, d in live.docs.items()}, **staging.stale_keys}
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
    load_bibs([config.DATA_PATH, *(Path(p).parent for p in list(live.docs) + live.stale_paths)])
    if config.STAGING_PATH.exists() or live.stale_paths:
        reindex()
        return
    with _job_lock:
        gone = [p for p in list(live.docs) if not Path(p).exists()]
        for p in gone:
            remove_file(Path(p), live)
        index_paths(scan(config.DATA_PATH), live, "startup")


def on_changed(path: Path) -> None:
    with _job_lock:
        if path.suffix.lower() == ".bib" and path.is_file():
            _bib_loaded.discard(path)
            load_bibs([path.parent])
            return
        if is_supported(path) and path.is_file() and ingest_file(path, store(), None, library):
            store().save()


def on_deleted(path: Path) -> None:
    with _job_lock:
        if remove_file(path, store()):
            store().save()

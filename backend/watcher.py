"""
Watches data/ recursively. Events are debounced per path (editors write files in several steps),
then re-indexed or removed. Each event costs O(1) to queue; the work is the ingest cost.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

import config
import indexer
from ingest import is_supported

DEBOUNCE_S = 1.5


class _Handler(FileSystemEventHandler):
    def __init__(self) -> None:
        self.pending: dict[Path, tuple[float, str]] = {}
        self.cond = threading.Condition()
        threading.Thread(target=self._drain, daemon=True).start()

    def _push(self, path: str, action: str) -> None:
        p = Path(path)
        if not is_supported(p):
            return
        with self.cond:
            self.pending[p] = (time.monotonic() + DEBOUNCE_S, action)
            self.cond.notify()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(event.src_path, "index")

    on_modified = on_created

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(event.src_path, "remove")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._push(event.src_path, "remove")
            self._push(event.dest_path, "index")

    def _drain(self) -> None:
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                now = time.monotonic()
                ready = [(p, a) for p, (t, a) in self.pending.items() if t <= now]
                for p, _ in ready:
                    del self.pending[p]
                if not ready:
                    self.cond.wait(min(t for t, _ in self.pending.values()) - now)
                    continue
            for p, action in ready:
                try:
                    indexer.on_changed(p) if action == "index" else indexer.on_deleted(p)
                except Exception as e:
                    print(f"[Quotely] Watcher error on {p.name}: {e}")


def start_watcher() -> Observer:
    config.DATA_PATH.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.daemon = True
    observer.schedule(_Handler(), str(config.DATA_PATH), recursive=True)
    observer.start()
    return observer

"""
Watchdog-based folder watcher: auto-ingests new files dropped anywhere under data/
(subdirectories included, data/db/ excluded).
"""
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer

from ingest import ingest_file, is_already_indexed, DATA_PATH, DB_PATH, get_collection

SUPPORTED_EXTENSIONS = {
    ".pdf", ".tex", ".docx", ".doc",
    ".md", ".txt",
    ".pptx", ".ppt", ".odt", ".rtf",
    ".xlsx", ".xls", ".csv",
    ".ipynb",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
}


def _is_db_file(path: Path) -> bool:
    """True if the file lives inside data/db/ (ChromaDB internal files)."""
    try:
        path.relative_to(DB_PATH)
        return True
    except ValueError:
        return False


class PapersHandler(FileSystemEventHandler):
    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        if _is_db_file(path):
            return
        # Small delay to ensure file is fully written
        time.sleep(0.5)
        try:
            if is_already_indexed(path):
                print(f"[RagCite] Already indexed, skipping: {path.name}")
                return
            ingest_file(path)
            get_collection().save()
        except Exception as e:
            print(f"[RagCite] Error ingesting {path.name}: {e}")


def start_watcher() -> Observer:
    """Start watching data/ recursively in a background thread. Returns observer."""
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(PapersHandler(), str(DATA_PATH), recursive=True)
    t = threading.Thread(target=observer.start, daemon=True)
    t.start()
    print(f"[RagCite] Watching {DATA_PATH} (recursive) for new documents...")
    return observer


def index_existing_papers():
    """Index any files already under data/ (all subdirs) that aren't yet indexed."""
    count = 0
    for path in DATA_PATH.rglob("*"):
        if path.is_dir():
            continue
        if _is_db_file(path):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if is_already_indexed(path):
            continue
        try:
            ingest_file(path)
            count += 1
        except Exception as e:
            print(f"[RagCite] Failed to index {path.name}: {e}")
    if count:
        get_collection().save()
        print(f"[RagCite] Startup indexing complete: {count} new document(s) added.")

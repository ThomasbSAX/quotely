from __future__ import annotations

import os
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent / "version.txt"
VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "dev"

ROOT = Path(os.environ.get("QUOTELY_HOME", Path(__file__).resolve().parent.parent))
DATA_PATH = ROOT / "data"
PAPERS_PATH = DATA_PATH / "papers"
DB_PATH = DATA_PATH / "db"
INDEX_PATH = DB_PATH / "index"
STAGING_PATH = DB_PATH / "staging"
LEGACY_PICKLE = DB_PATH / "vectors.pkl"

HOST = "127.0.0.1"
PORT = int(os.environ.get("QUOTELY_PORT", "7331"))
CLIENT_HEADER = "X-Quotely-Client"

EMBED_MODEL = os.environ.get("QUOTELY_EMBED_MODEL", "intfloat/multilingual-e5-small")
EMBED_MODEL_PATH = os.environ.get("QUOTELY_EMBED_MODEL_PATH", "")
EMBED_DIM = int(os.environ.get("QUOTELY_EMBED_DIM", "384"))
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
RERANKER_MODEL = os.environ.get("QUOTELY_RERANKER", "jinaai/jina-reranker-v2-base-multilingual")
INDEX_SIGNATURE = f"{EMBED_MODEL}|{EMBED_MODEL_PATH}|e5-prefix|chunk-v2"

CHUNK_WORDS = 200
CHUNK_OVERLAP = 40
MIN_CHUNK_WORDS = 8
EMBED_BATCH = 64

POOL_PER_RESULT = 20
CHUNKS_PER_PAPER = 2
RERANK_TOP = 20
RERANK_MAX_WORDS = 256

SAVE_EVERY_FILES = 10

SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".tex", ".docx", ".doc",
    ".md", ".txt",
    ".pptx", ".ppt", ".odt", ".rtf",
    ".xlsx", ".xls", ".csv",
    ".ipynb",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
})

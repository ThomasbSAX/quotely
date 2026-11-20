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
MODELS_PATH = Path(os.environ.get("FASTEMBED_CACHE_PATH", DB_PATH / "models"))

HOST = "127.0.0.1"
PORT = int(os.environ.get("QUOTELY_PORT", "7331"))
CLIENT_HEADER = "X-Quotely-Client"

EMBED_CANDIDATES = [
    (os.environ["QUOTELY_EMBED_MODEL"], os.environ.get("QUOTELY_EMBED_FILE", "onnx/model.onnx"))
] if os.environ.get("QUOTELY_EMBED_MODEL") else [
    ("Xenova/multilingual-e5-small", "onnx/model_quantized.onnx"),
    ("intfloat/multilingual-e5-small", "onnx/model.onnx"),
]
EMBED_MODEL_PATH = os.environ.get("QUOTELY_EMBED_MODEL_PATH", "")
EMBED_DIM = int(os.environ.get("QUOTELY_EMBED_DIM", "384"))
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
RERANKER_MODEL = os.environ.get("QUOTELY_RERANKER", "jinaai/jina-reranker-v2-base-multilingual")
RERANKER_FILE = os.environ.get("QUOTELY_RERANKER_FILE", "onnx/model_quantized.onnx")
RERANK_BATCH = 4
PRECISION_MODE = os.environ.get("QUOTELY_PRECISION", "0") == "1"
ONLINE_METADATA = os.environ.get("QUOTELY_ONLINE_METADATA", "0") == "1"
OCR_SCANNED_PAGES = os.environ.get("QUOTELY_OCR", "1") == "1"
INDEX_VERSION = "passages-v4"

CHUNK_WORDS = 120
MIN_CHUNK_WORDS = 12
EMBED_BATCH = 4

DENSE_POOL = 200
RRF_K = 60
CHUNKS_PER_PAPER = 2
RERANK_TOP = 12
RERANK_MAX_WORDS = 120

ASSIST_MIN_WORDS = 8
ASSIST_MAX_SENTENCES = 12
ASSIST_THRESHOLD = float(os.environ.get("QUOTELY_ASSIST_THRESHOLD", "0.82"))
ASSIST_SECONDARY_MARGIN = 0.02
ASSIST_GENERIC_MARGIN = float(os.environ.get("QUOTELY_ASSIST_GENERIC_MARGIN", "0.065"))
ASSIST_CONTRADICTION = os.environ.get("QUOTELY_CONTRADICTION", "1") == "1"

SAVE_EVERY_FILES = 10

SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".tex", ".docx", ".doc",
    ".md", ".txt",
    ".pptx", ".ppt", ".odt", ".rtf",
    ".xlsx", ".xls", ".csv",
    ".ipynb",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
})

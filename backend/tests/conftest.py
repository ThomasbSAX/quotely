import hashlib
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

os.environ["QUOTELY_HOME"] = tempfile.mkdtemp(prefix="quotely-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import embeddings
import indexer

DIM = config.EMBED_DIM


def _bow(text: str) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for w in text.lower().split():
        w = w.strip(".,;:!?()[]{}\"'")
        if len(w) > 2:
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
    return v / (np.linalg.norm(v) + 1e-12)


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_passages", lambda texts: np.stack([_bow(t) for t in texts]) if texts else np.zeros((0, DIM), np.float32))
    monkeypatch.setattr(embeddings, "embed_queries", lambda texts: np.stack([_bow(t) for t in texts]))
    monkeypatch.setattr(embeddings, "model_id", lambda: "fake-bow")
    monkeypatch.setattr(embeddings, "rerank", lambda q, docs: None)
    yield


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "DATA_PATH", tmp_path / "data")
    monkeypatch.setattr(config, "PAPERS_PATH", tmp_path / "data" / "papers")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "db")
    monkeypatch.setattr(config, "INDEX_PATH", tmp_path / "data" / "db" / "index")
    monkeypatch.setattr(config, "STAGING_PATH", tmp_path / "data" / "db" / "staging")
    monkeypatch.setattr(config, "LEGACY_PICKLE", tmp_path / "data" / "db" / "vectors.pkl")
    config.PAPERS_PATH.mkdir(parents=True)
    monkeypatch.setattr(indexer, "_store", None)
    return tmp_path


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path

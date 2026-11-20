"""
Passage store: a contiguous float32 matrix of L2-normalised rows plus lexical statistics.

Rows are passages; per-document metadata (key, title, BibTeX…) is stored once per document.
N rows, d dim, D documents, T tokens in a passage, k results.
  add_document(m rows)  O(m·d + m·T) amortised (capacity doubling, df update)
  remove_document        O(N·d) compaction + O(m·T) df update (only when present)
  dense_top              O(N·d) matmul + O(N) argpartition + O(k log k)
  allowed_mask           O(N) once per (scopes, version), then cached
  bm25 on a pool of P    O(P·T)
  save                   O(N·d) sequential write, atomic directory swap
  load                   O(N·d) (lengths and document frequencies are persisted)
"""
from __future__ import annotations

import json
import math
import pickle
import re
import shutil
import threading
from collections import Counter
from pathlib import Path

import numpy as np

_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def normalise(x) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[None, :]
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)


def under(path: str, scopes: tuple[str, ...]) -> bool:
    return any(path == s or path.startswith(s.rstrip("/\\") + "/") or path.startswith(s.rstrip("/\\") + "\\") for s in scopes)


class VectorStore:
    def __init__(self, path: Path, signature: str, dim: int) -> None:
        self.path = Path(path)
        self.signature = signature
        self.dim = dim
        self.lock = threading.RLock()
        self.stale_keys: dict[str, str] = {}
        self._clear()
        self._load()

    @property
    def stale_paths(self) -> list[str]:
        return sorted(self.stale_keys)

    def _clear(self) -> None:
        self._emb = np.zeros((0, self.dim), dtype=np.float32)
        self._n = 0
        self.texts: list[str] = []
        self.rows: list[dict] = []
        self.docs: dict[str, dict] = {}
        self._keys: dict[str, str] = {}
        self._df: Counter = Counter()
        self._lengths: list[int] = []
        self._version = 0
        self._mask_cache: tuple | None = None
        self.dirty = False

    def _load(self) -> None:
        old = self.path.with_name(self.path.name + ".old")
        if not self.path.exists() and old.exists():
            old.rename(self.path)
        records_file = self.path / "records.json"
        if records_file.exists():
            records = json.loads(records_file.read_text(encoding="utf-8"))
            if records.get("signature") != self.signature:
                docs = records.get("docs")
                if docs is not None:
                    self.stale_keys = {p: d["bibtex_key"] for p, d in docs.items()}
                else:
                    self.stale_keys = {m["file_path"]: m["bibtex_key"] for m in records.get("metadatas", [])}
                return
            emb = np.load(self.path / "embeddings.npy")
            self.docs = records["docs"]
            self._keys = {d["bibtex_key"]: p for p, d in self.docs.items()}
            stats = (records["lengths"], records["df"]) if "lengths" in records else None
            self._push(emb, records["texts"], records["rows"], stats)
            self.dirty = False
            return
        legacy = self.path.parent / "vectors.pkl"
        if legacy.exists():
            with open(legacy, "rb") as f:
                data = pickle.load(f)
            self.stale_keys = {m["file_path"]: m["bibtex_key"] for m in data.get("metadatas", []) if m.get("file_path")}

    def save(self) -> None:
        with self.lock:
            if not self.dirty and self.path.exists():
                return
            tmp = self.path.with_name(self.path.name + ".tmp")
            old = self.path.with_name(self.path.name + ".old")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            np.save(tmp / "embeddings.npy", self._emb[: self._n])
            (tmp / "records.json").write_text(json.dumps({
                "signature": self.signature, "docs": self.docs, "texts": self.texts, "rows": self.rows,
                "lengths": self._lengths, "df": self._df,
            }, ensure_ascii=False), encoding="utf-8")
            shutil.rmtree(old, ignore_errors=True)
            if self.path.exists():
                self.path.rename(old)
            tmp.rename(self.path)
            shutil.rmtree(old, ignore_errors=True)
            self.dirty = False

    def _touch(self) -> None:
        self._version += 1
        self._mask_cache = None
        self.dirty = True

    def _push(self, emb, texts: list[str], rows: list[dict], stats: tuple | None = None) -> None:
        m = len(texts)
        if self._n + m > self._emb.shape[0]:
            grown = np.zeros((max(self._n + m, 2 * self._emb.shape[0], 256), self.dim), dtype=np.float32)
            grown[: self._n] = self._emb[: self._n]
            self._emb = grown
        if m:
            self._emb[self._n: self._n + m] = normalise(emb)
        if stats is not None:
            self._lengths.extend(stats[0])
            self._df.update(stats[1])
        else:
            for text in texts:
                toks = tokenize(text)
                self._lengths.append(len(toks))
                self._df.update(set(toks))
        self.texts.extend(texts)
        self.rows.extend(rows)
        self._n += m
        self._touch()

    def add_document(self, path: str, doc: dict, emb, texts: list[str], rows: list[dict]) -> None:
        with self.lock:
            self.remove_document(path)
            self.docs[path] = doc
            self._keys[doc["bibtex_key"]] = path
            self._push(emb, texts, [{**r, "path": path} for r in rows])

    def remove_document(self, path: str) -> bool:
        with self.lock:
            doc = self.docs.pop(path, None)
            if doc is None:
                return False
            self._keys.pop(doc["bibtex_key"], None)
            keep = np.fromiter((r["path"] != path for r in self.rows), dtype=bool, count=self._n)
            for i in np.flatnonzero(~keep):
                self._df.subtract(set(tokenize(self.texts[i])))
            self._df += Counter()
            kept = int(keep.sum())
            self._emb[:kept] = self._emb[: self._n][keep]
            self.texts = [t for t, k in zip(self.texts, keep) if k]
            self.rows = [r for r, k in zip(self.rows, keep) if k]
            self._lengths = [n for n, k in zip(self._lengths, keep) if k]
            self._n = kept
            self._touch()
            return True

    def reset(self) -> None:
        with self.lock:
            self._clear()
            self.dirty = True

    def count(self) -> int:
        return self._n

    def doc(self, path: str) -> dict | None:
        return self.docs.get(path)

    def key_owner(self, key: str) -> str | None:
        return self._keys.get(key)

    def doc_by_key(self, key: str) -> dict | None:
        path = self._keys.get(key)
        return self.docs.get(path) if path else None

    def rows_of(self, key: str) -> list[int]:
        path = self._keys.get(key)
        return [i for i, r in enumerate(self.rows) if r["path"] == path]

    def allowed_mask(self, scopes: tuple[str, ...]) -> np.ndarray | None:
        if not scopes:
            return None
        cache = self._mask_cache
        if cache and cache[0] == (scopes, self._version):
            return cache[1]
        ok = {p for p in self.docs if under(p, scopes)}
        mask = np.fromiter((r["path"] in ok for r in self.rows), dtype=bool, count=self._n)
        self._mask_cache = ((scopes, self._version), mask)
        return mask

    def dense_top(self, queries, k: int, scopes: tuple[str, ...] = ()) -> list[list[tuple[int, float]]]:
        with self.lock:
            if self._n == 0:
                return [[] for _ in range(len(np.atleast_2d(queries)))]
            sims = normalise(queries) @ self._emb[: self._n].T
            mask = self.allowed_mask(scopes)
            if mask is not None:
                sims[:, ~mask] = -np.inf
            k = min(k, self._n)
            top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
            out = []
            for q, idx in enumerate(top):
                idx = idx[np.argsort(-sims[q, idx])]
                out.append([(int(i), float(sims[q, i])) for i in idx if np.isfinite(sims[q, i])])
            return out

    def bm25(self, query: str, rows: list[int], k1: float = 1.2, b: float = 0.75) -> list[float]:
        terms = set(tokenize(query))
        if not terms or not rows:
            return [0.0] * len(rows)
        n = max(self._n, 1)
        avg = sum(self._lengths) / n or 1.0
        idf = {t: math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5)) for t in terms}
        scores = []
        for i in rows:
            tf = Counter(t for t in tokenize(self.texts[i]) if t in terms)
            norm = k1 * (1 - b + b * self._lengths[i] / avg)
            scores.append(sum(idf[t] * c * (k1 + 1) / (c + norm) for t, c in tf.items()))
        return scores

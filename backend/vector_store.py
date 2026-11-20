"""
Dense vector store on a contiguous float32 matrix of L2-normalised rows.

N = stored chunks, d = embedding dim, k = requested results, D = distinct documents.
  upsert(m rows)     O(m·d) amortised (capacity doubling)
  delete_where       O(N·d) (compaction)
  query              O(N·d) matmul + O(N) argpartition + O(k log k)
  allowed_mask       O(N) once per (prefixes, store version), then cached
  save               O(N·d) sequential write, atomic directory swap
"""
from __future__ import annotations

import json
import pickle
import shutil
import threading
from collections import Counter
from pathlib import Path

import numpy as np


def _normalise(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[None, :]
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/\\") + "/") or path.startswith(p.rstrip("/\\") + "\\") for p in prefixes)


class VectorStore:
    def __init__(self, path: Path, signature: str, dim: int) -> None:
        self.path = Path(path)
        self.signature = signature
        self.dim = dim
        self.lock = threading.RLock()
        self.stale_paths: list[str] = []
        self._clear()
        self._load()

    def _clear(self) -> None:
        self._emb = np.zeros((0, self.dim), dtype=np.float32)
        self._n = 0
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict] = []
        self._pos: dict[str, int] = {}
        self._hashes: Counter = Counter()
        self._paths: Counter = Counter()
        self._keys: dict[str, str] = {}
        self._path_key: dict[str, str] = {}
        self._path_hash: dict[str, str] = {}
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
            emb = np.load(self.path / "embeddings.npy")
            if records.get("signature") != self.signature or emb.shape[1:] != (self.dim,):
                self.stale_paths = sorted({m["file_path"] for m in records["metadatas"]})
                return
            self._append(records["ids"], emb, records["documents"], records["metadatas"])
            self.dirty = False
            return
        legacy = self.path.parent / "vectors.pkl"
        if legacy.exists():
            with open(legacy, "rb") as f:
                data = pickle.load(f)
            self.stale_paths = sorted({m.get("file_path", "") for m in data.get("metadatas", [])} - {""})

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
                "signature": self.signature,
                "ids": self.ids,
                "documents": self.documents,
                "metadatas": self.metadatas,
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

    def _reserve(self, extra: int) -> None:
        need = self._n + extra
        if need <= self._emb.shape[0]:
            return
        cap = max(need, 2 * self._emb.shape[0], 256)
        grown = np.zeros((cap, self.dim), dtype=np.float32)
        grown[: self._n] = self._emb[: self._n]
        self._emb = grown

    def _append(self, ids, emb, docs, metas) -> None:
        emb = _normalise(emb) if len(ids) else np.zeros((0, self.dim), dtype=np.float32)
        self._reserve(len(ids))
        for id_, vec, doc, meta in zip(ids, emb, docs, metas):
            if id_ in self._pos:
                i = self._pos[id_]
                self._unindex(self.metadatas[i])
                self._emb[i] = vec
                self.documents[i] = doc
                self.metadatas[i] = meta
            else:
                i = self._n
                self._pos[id_] = i
                self._emb[i] = vec
                self.ids.append(id_)
                self.documents.append(doc)
                self.metadatas.append(meta)
                self._n += 1
            self._index(meta)
        self._touch()

    def _index(self, meta: dict) -> None:
        self._hashes[meta["file_hash"]] += 1
        self._paths[meta["file_path"]] += 1
        self._keys[meta["bibtex_key"]] = meta["file_path"]
        self._path_key[meta["file_path"]] = meta["bibtex_key"]
        self._path_hash[meta["file_path"]] = meta["file_hash"]

    def _unindex(self, meta: dict) -> None:
        for counter, value in ((self._hashes, meta["file_hash"]), (self._paths, meta["file_path"])):
            counter[value] -= 1
            if counter[value] <= 0:
                del counter[value]
        if meta["file_path"] not in self._paths:
            self._keys.pop(meta["bibtex_key"], None)
            self._path_key.pop(meta["file_path"], None)
            self._path_hash.pop(meta["file_path"], None)

    def upsert(self, ids: list[str], embeddings, documents: list[str], metadatas: list[dict]) -> None:
        with self.lock:
            self._append(ids, embeddings, documents, metadatas)

    def delete_where(self, key: str, value: str) -> int:
        with self.lock:
            keep = [i for i, m in enumerate(self.metadatas[: self._n]) if m.get(key) != value]
            removed = self._n - len(keep)
            if removed:
                self._compact(keep)
            return removed

    def _compact(self, keep: list[int]) -> None:
        emb = self._emb[keep].copy() if keep else np.zeros((0, self.dim), dtype=np.float32)
        ids = [self.ids[i] for i in keep]
        docs = [self.documents[i] for i in keep]
        metas = [self.metadatas[i] for i in keep]
        self._clear()
        self._append(ids, emb, docs, metas)

    def reset(self) -> None:
        with self.lock:
            self._clear()
            self.dirty = True

    def count(self) -> int:
        return self._n

    def has_hash(self, sha: str) -> bool:
        return sha in self._hashes

    def has_path(self, path: str) -> bool:
        return path in self._paths

    def file_paths(self) -> list[str]:
        return list(self._paths)

    def key_owner(self, key: str) -> str | None:
        return self._keys.get(key)

    def key_for_path(self, path: str) -> str | None:
        return self._path_key.get(path)

    def path_hash(self, path: str) -> str | None:
        return self._path_hash.get(path)

    def rows_where(self, key: str, value) -> list[int]:
        return [i for i, m in enumerate(self.metadatas[: self._n]) if m.get(key) == value]

    def allowed_mask(self, prefixes: tuple[str, ...]) -> np.ndarray | None:
        if not prefixes:
            return None
        cache = self._mask_cache
        if cache and cache[0] == (prefixes, self._version):
            return cache[1]
        ok_paths = {p for p in self._paths if _under(p, prefixes)}
        mask = np.fromiter((m["file_path"] in ok_paths for m in self.metadatas[: self._n]), dtype=bool, count=self._n)
        self._mask_cache = ((prefixes, self._version), mask)
        return mask

    def query(self, vector, k: int, prefixes: tuple[str, ...] = ()) -> list[tuple[int, float]]:
        with self.lock:
            if self._n == 0:
                return []
            sims = self._emb[: self._n] @ _normalise(vector)[0]
            mask = self.allowed_mask(prefixes)
            if mask is not None:
                sims = np.where(mask, sims, -np.inf)
            k = min(k, self._n)
            top = np.argpartition(-sims, k - 1)[:k]
            top = top[np.argsort(-sims[top])]
            return [(int(i), float(sims[i])) for i in top if np.isfinite(sims[i])]

"""
Pure-Python vector store using numpy for cosine similarity search.
Replaces ChromaDB to avoid C-extension (hnswlib / chroma-hnswlib) crashes
on macOS 26 beta + Python 3.12 ARM64.

Persistence: single pickle file at data/db/vectors.pkl
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np


class VectorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ids:        list[str]        = []
        self._embeddings: list[list[float]] = []
        self._documents:  list[str]        = []
        self._metadatas:  list[dict]       = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.db_path.exists():
            return
        try:
            with open(self.db_path, "rb") as f:
                data = pickle.load(f)
            self._ids        = data.get("ids", [])
            self._embeddings = data.get("embeddings", [])
            self._documents  = data.get("documents", [])
            self._metadatas  = data.get("metadatas", [])
        except Exception as e:
            print(f"[VectorStore] Failed to load DB, starting fresh: {e}")
            self._reset_data()

    def _save(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.db_path, "wb") as f:
            pickle.dump({
                "ids":        self._ids,
                "embeddings": self._embeddings,
                "documents":  self._documents,
                "metadatas":  self._metadatas,
            }, f)

    def _reset_data(self) -> None:
        self._ids        = []
        self._embeddings = []
        self._documents  = []
        self._metadatas  = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        ids:        list[str],
        embeddings: list[list[float]],
        documents:  list[str],
        metadatas:  list[dict],
    ) -> None:
        id_index = {id_: i for i, id_ in enumerate(self._ids)}
        for id_, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            if id_ in id_index:
                idx = id_index[id_]
                self._embeddings[idx] = emb
                self._documents[idx]  = doc
                self._metadatas[idx]  = meta
            else:
                id_index[id_] = len(self._ids)
                self._ids.append(id_)
                self._embeddings.append(emb)
                self._documents.append(doc)
                self._metadatas.append(meta)
        self._save()

    def delete(
        self,
        where: dict | None = None,
        ids:   list[str] | None = None,
    ) -> None:
        if ids is not None:
            drop = set(ids)
            keep = [i for i, id_ in enumerate(self._ids) if id_ not in drop]
        elif where:
            key, value = next(iter(where.items()))
            keep = [i for i, m in enumerate(self._metadatas) if m.get(key) != value]
        else:
            keep = []
        self._ids        = [self._ids[i]        for i in keep]
        self._embeddings = [self._embeddings[i] for i in keep]
        self._documents  = [self._documents[i]  for i in keep]
        self._metadatas  = [self._metadatas[i]  for i in keep]
        self._save()

    def reset(self) -> None:
        self._reset_data()
        self._save()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def count(self) -> int:
        return len(self._ids)

    def get(
        self,
        where:          dict | None = None,
        where_document: dict | None = None,
        limit:          int  | None = None,
        include:        list | None = None,
    ) -> dict:
        indices = list(range(len(self._ids)))

        # Filter by metadata field
        if where:
            key, value = next(iter(where.items()))
            indices = [i for i in indices if self._metadatas[i].get(key) == value]

        # Filter by document full-text substring
        if where_document and "$contains" in where_document:
            needle = where_document["$contains"].lower()
            indices = [i for i in indices if needle in self._documents[i].lower()]

        if limit is not None:
            indices = indices[:limit]

        result: dict = {"ids": [self._ids[i] for i in indices]}

        # include=[] means "ids only"; include=None means "all"
        want_meta = include is None or "metadatas" in include
        want_docs = include is None or "documents" in include

        if want_meta:
            result["metadatas"] = [self._metadatas[i] for i in indices]
        if want_docs:
            result["documents"] = [self._documents[i] for i in indices]

        return result

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results:        int       = 10,
        include:          list[str] | None = None,
    ) -> dict:
        if not self._embeddings:
            return {"metadatas": [[]], "distances": [[]], "documents": [[]]}

        query_vec  = np.array(query_embeddings[0], dtype=np.float32)
        emb_matrix = np.array(self._embeddings,    dtype=np.float32)

        # Cosine similarity (normalised dot product)
        query_norm  = query_vec  / (np.linalg.norm(query_vec)                              + 1e-8)
        emb_norms   = np.linalg.norm(emb_matrix, axis=1, keepdims=True) + 1e-8
        similarities = (emb_matrix / emb_norms) @ query_norm

        top_k   = min(n_results, len(self._ids))
        top_idx = np.argsort(similarities)[::-1][:top_k]

        return {
            "metadatas": [[self._metadatas[i] for i in top_idx]],
            "distances":  [[float(1.0 - similarities[i]) for i in top_idx]],
            "documents":  [[self._documents[i]  for i in top_idx]],
        }

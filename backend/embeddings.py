"""
ONNX models through fastembed (no PyTorch at inference).

multilingual-e5-small is not in fastembed's built-in catalogue, so it is registered as a
custom model (mean pooling, L2 norm). A fine-tuned export can be used instead through
QUOTELY_EMBED_MODEL_PATH (directory holding model.onnx + tokenizer files).

Cost: one forward pass per text, O(L²·h) per layer for L tokens (L ≤ 512).
"""
from __future__ import annotations

import threading

import numpy as np

import config

_lock = threading.Lock()
_embedder = None
_reranker = None


def _register_e5() -> None:
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    known = {m["model"] for m in TextEmbedding.list_supported_models()}
    if config.EMBED_MODEL in known:
        return
    TextEmbedding.add_custom_model(
        model=config.EMBED_MODEL,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=config.EMBED_MODEL),
        dim=config.EMBED_DIM,
        model_file="onnx/model.onnx",
    )


def embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            from fastembed import TextEmbedding

            _register_e5()
            if config.EMBED_MODEL_PATH:
                _embedder = TextEmbedding(config.EMBED_MODEL, specific_model_path=config.EMBED_MODEL_PATH)
            else:
                _embedder = TextEmbedding(config.EMBED_MODEL)
            print(f"[Quotely] Embedding model ready ({config.EMBED_MODEL}).")
    return _embedder


def reranker():
    global _reranker
    with _lock:
        if _reranker is None:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                _reranker = TextCrossEncoder(config.RERANKER_MODEL)
                print(f"[Quotely] Reranker ready ({config.RERANKER_MODEL}).")
            except Exception as e:
                print(f"[Quotely] Reranker disabled: {e}")
                _reranker = False
    return _reranker or None


def embed_passages(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
    batch = [config.PASSAGE_PREFIX + t for t in texts]
    return np.asarray(list(embedder().embed(batch, batch_size=config.EMBED_BATCH)), dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    return np.asarray(next(iter(embedder().embed([config.QUERY_PREFIX + text]))), dtype=np.float32)


def rerank(query: str, docs: list[str]) -> list[float] | None:
    model = reranker()
    if model is None or len(docs) < 2:
        return None
    return [float(s) for s in model.rerank(query, docs)]


def status() -> dict:
    return {"model_loaded": bool(_embedder), "reranker_loaded": bool(_reranker)}

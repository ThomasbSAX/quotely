"""
ONNX models through fastembed (no PyTorch at inference).

multilingual-e5-small is not in fastembed's catalogue, so it is registered as a custom model
(mean pooling, L2 norm). The int8 export (~120 MB) is tried first for speed and footprint, the
fp32 export (~470 MB) second. Models are cached in data/db/models, outside the scanned
library and away from the temp directory macOS purges, so the backend keeps working offline.
QUOTELY_EMBED_MODEL_PATH points to a local export (e.g. fine-tuned).

Reranker ("precise" mode): jina-reranker-v2 multilingual, int8 export (~280 MB instead of 1.1 GB for
fp32, same ranking on the benchmark), scored in batches of RERANK_BATCH to cap memory (~0.9 GB
instead of ~2.7 GB).

Cost: one forward pass per text, O(L²·h) per layer for L tokens (L ≤ 512). Query embeddings are
memoised (LRU) so re-analysing an unchanged sentence costs O(1).
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np

import config

_lock = threading.Lock()
_embedder = None
_model_id = ""
_reranker = None
_query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
_QUERY_CACHE_SIZE = 2048


def _load(name: str, model_file: str):
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    if name not in {m["model"] for m in TextEmbedding.list_supported_models()}:
        TextEmbedding.add_custom_model(
            model=name, pooling=PoolingType.MEAN, normalization=True,
            sources=ModelSource(hf=name), dim=config.EMBED_DIM, model_file=model_file,
        )
    kwargs = {"specific_model_path": config.EMBED_MODEL_PATH} if config.EMBED_MODEL_PATH else {}
    return TextEmbedding(name, cache_dir=str(config.MODELS_PATH), **kwargs)


def embedder():
    global _embedder, _model_id
    with _lock:
        if _embedder is None:
            errors = []
            for name, model_file in config.EMBED_CANDIDATES:
                try:
                    _embedder = _load(name, model_file)
                    _model_id = f"{name}:{model_file}:{config.EMBED_MODEL_PATH}"
                    print(f"[Quotely] Embedding model ready ({name}, {model_file}).")
                    break
                except Exception as e:
                    errors.append(f"{name}: {e}")
            if _embedder is None:
                raise RuntimeError("No embedding model could be loaded: " + " | ".join(errors))
    return _embedder


def model_id() -> str:
    embedder()
    return _model_id


def reranker():
    global _reranker
    with _lock:
        if _reranker is None:
            try:
                from fastembed.common.model_description import ModelSource
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                name = f"{config.RERANKER_MODEL}:{config.RERANKER_FILE}" if config.RERANKER_FILE else config.RERANKER_MODEL
                if name not in {m["model"] for m in TextCrossEncoder.list_supported_models()}:
                    TextCrossEncoder.add_custom_model(model=name, sources=ModelSource(hf=config.RERANKER_MODEL),
                                                      model_file=config.RERANKER_FILE)
                _reranker = TextCrossEncoder(name, cache_dir=str(config.MODELS_PATH))
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


def embed_queries(texts: list[str]) -> np.ndarray:
    missing = [t for t in dict.fromkeys(texts) if t not in _query_cache]
    if missing:
        vecs = embedder().embed([config.QUERY_PREFIX + t for t in missing], batch_size=config.EMBED_BATCH)
        for t, v in zip(missing, vecs):
            _query_cache[t] = np.asarray(v, dtype=np.float32)
            if len(_query_cache) > _QUERY_CACHE_SIZE:
                _query_cache.popitem(last=False)
    for t in texts:
        _query_cache.move_to_end(t)
    return np.stack([_query_cache[t] for t in texts]) if texts else np.zeros((0, config.EMBED_DIM), np.float32)


def rerank(query: str, docs: list[str]) -> list[float] | None:
    model = reranker()
    if model is None or len(docs) < 2:
        return None
    return [float(s) for s in model.rerank(query, docs, batch_size=config.RERANK_BATCH)]


def status() -> dict:
    return {"model_loaded": bool(_embedder), "model": _model_id, "reranker_loaded": bool(_reranker)}

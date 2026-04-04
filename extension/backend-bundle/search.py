"""
Semantic search: bi-encoder retrieval + optional cross-encoder reranker.
Both models use fastembed (ONNX) — no PyTorch, no GPU required.
"""
from __future__ import annotations

import math
import re

from ingest import get_collection, get_model
from models import CitationResult

_reranker = None          # TextCrossEncoder, loaded lazily
_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANK_MAX_WORDS = 350
_CHUNKS_PER_PAPER = 2


def get_reranker():
    global _reranker
    if _reranker is None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder  # lazy
            print(f"[RagCite] Loading reranker ({_RERANKER_MODEL})...")
            _reranker = TextCrossEncoder(_RERANKER_MODEL)
            print("[RagCite] Reranker ready.")
        except Exception as e:
            print(f"[RagCite] Reranker unavailable ({e}), using bi-encoder only.")
            _reranker = False  # sentinel: reranking disabled
    return _reranker if _reranker is not False else None


def _clean_query(text: str) -> str:
    text = re.sub(r"\\(?:textbf|textit|emph|underline|text)\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\(?:cite|ref|label|footnote)\{[^}]*\}", "", text)
    text = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?\{[^}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text


def search(text: str, n: int = 5, workspace_path: str = "") -> list[CitationResult]:
    """Two-stage retrieval: bi-encoder (ONNX) → optional cross-encoder reranker (ONNX)."""
    model = get_model()
    clean_text = _clean_query(text)

    # fastembed: embed() returns a generator of numpy arrays
    query_embedding = [next(model.embed([clean_text])).tolist()]

    col = get_collection()
    total = col.count()
    if total == 0:
        return []

    pool_size = min(n * 20, total)
    results = col.query(
        query_embeddings=query_embedding,
        n_results=pool_size,
        include=["metadatas", "distances", "documents"],
    )

    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    documents = results["documents"][0]

    # Deduplicate: keep top-2 chunks per paper for richer reranker context
    best: dict[str, list[tuple[dict, float, str]]] = {}
    for meta, dist, doc in zip(metadatas, distances, documents):
        key = meta["bibtex_key"]
        if key not in best:
            best[key] = []
        if len(best[key]) < _CHUNKS_PER_PAPER:
            best[key].append((meta, dist, doc))

    unique_papers: list[tuple[dict, float, str]] = []
    for chunks in best.values():
        best_meta, best_dist, _ = chunks[0]
        combined = " [...] ".join(c[2] for c in chunks)
        combined = _truncate(combined, _RERANK_MAX_WORDS)
        unique_papers.append((best_meta, best_dist, combined))

    # Stage 2: rerank with cross-encoder (ONNX) if available
    reranker = get_reranker()
    if reranker is not None and len(unique_papers) >= 2:
        try:
            docs_for_rerank = [
                f"{meta['title']}. {context}"
                for meta, _, context in unique_papers
            ]
            raw = list(reranker.rerank(clean_text, docs_for_rerank))
            # fastembed reranker returns objects with .score, or plain floats
            if raw and hasattr(raw[0], "score"):
                reranker_scores = [r.score for r in raw]
            else:
                reranker_scores = [float(r) for r in raw]

            ranked = sorted(
                zip(unique_papers, reranker_scores),
                key=lambda x: x[1],
                reverse=True,
            )
            unique_papers = [item for item, _ in ranked]
            scores_norm = [round(1 / (1 + math.exp(-s)), 4) for _, s in ranked]
        except Exception as e:
            print(f"[RagCite] Reranker error ({e}), falling back to bi-encoder.")
            unique_papers.sort(key=lambda x: x[1])
            scores_norm = [round(max(0.0, 1.0 - dist), 4) for _, dist, _ in unique_papers]
    else:
        unique_papers.sort(key=lambda x: x[1])
        scores_norm = [round(max(0.0, 1.0 - dist), 4) for _, dist, _ in unique_papers]

    # Filter to workspace if provided
    if workspace_path:
        filtered = [
            (paper, score)
            for paper, score in zip(unique_papers, scores_norm)
            if paper[0].get("file_path", "").startswith(workspace_path)
        ]
        if filtered:
            unique_papers = [p for p, _ in filtered]
            scores_norm = [s for _, s in filtered]

    citations: list[CitationResult] = []
    for (meta, _, _), score in zip(unique_papers[:n], scores_norm[:n]):
        citations.append(
            CitationResult(
                bibtex_key=meta["bibtex_key"],
                title=meta["title"],
                authors=meta["authors"],
                year=meta["year"],
                score=score,
                bibtex_entry=meta["bibtex_entry"],
                file_path=meta.get("file_path", ""),
            )
        )

    return citations

"""
Semantic search: bi-encoder retrieval + cross-encoder reranker (2-stage pipeline)

Improvements over baseline:
- Query cleaning: LaTeX commands stripped before embedding
- Multi-chunk context: top-2 chunks per paper concatenated for reranker
- Wider pool: n×10 candidates before deduplication
"""
from __future__ import annotations

import math
import re

from sentence_transformers import CrossEncoder

from ingest import get_collection, get_model
from models import CitationResult

_reranker: CrossEncoder | None = None
RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

# Max words fed to cross-encoder per (query, doc) pair
_RERANK_MAX_WORDS = 350
# Max chunks kept per paper for building reranker context
_CHUNKS_PER_PAPER = 2


def get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        print(f"[RagCite] Loading reranker ({RERANKER_MODEL})...")
        _reranker = CrossEncoder(RERANKER_MODEL)
        print("[RagCite] Reranker ready.")
    return _reranker


def _clean_query(text: str) -> str:
    """
    Strip LaTeX markup from the query so the embedding model sees plain text.
    Keeps mathematical content by preserving $...$ inline math.
    """
    # Keep the content of formatting commands
    text = re.sub(r"\\(?:textbf|textit|emph|underline|text)\{([^}]+)\}", r"\1", text)
    # Remove cite/ref/label commands entirely
    text = re.sub(r"\\(?:cite|ref|label|footnote)\{[^}]*\}", "", text)
    # Remove environments wrappers (begin/end) but keep their content
    text = re.sub(r"\\(?:begin|end)\{[^}]+\}", "", text)
    # Remove remaining single-argument commands (figures, includes…)
    text = re.sub(r"\\[a-zA-Z]+\*?\{[^}]*\}", "", text)
    # Remove standalone commands
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text


def search(text: str, n: int = 5, workspace_path: str = "") -> list[CitationResult]:
    """Two-stage retrieval: bi-encoder → cross-encoder reranker → top-n citations.
    If workspace_path is provided, only papers whose file_path starts with it are returned.
    """
    model = get_model()

    # Clean LaTeX from query before embedding
    clean_text = _clean_query(text)
    query_embedding = model.encode([clean_text], show_progress_bar=False).tolist()

    col = get_collection()
    total = col.count()
    if total == 0:
        return []

    # Fetch a larger pool so we still have n results after workspace filtering
    pool_size = min(n * 20, total)
    results = col.query(
        query_embeddings=query_embedding,
        n_results=pool_size,
        include=["metadatas", "distances", "documents"],
    )

    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    documents = results["documents"][0]

    # Deduplicate: keep best N chunks per paper for richer reranker context
    best: dict[str, list[tuple[dict, float, str]]] = {}
    for meta, dist, doc in zip(metadatas, distances, documents):
        key = meta["bibtex_key"]
        if key not in best:
            best[key] = []
        if len(best[key]) < _CHUNKS_PER_PAPER:
            best[key].append((meta, dist, doc))

    # Build per-paper entries: (best_meta, best_dist, combined_context)
    unique_papers: list[tuple[dict, float, str]] = []
    for chunks in best.values():
        best_meta, best_dist, _ = chunks[0]
        combined = " [...] ".join(c[2] for c in chunks)
        combined = _truncate(combined, _RERANK_MAX_WORDS)
        unique_papers.append((best_meta, best_dist, combined))

    # Stage 2: rerank with cross-encoder
    if len(unique_papers) >= 2:
        reranker = get_reranker()
        pairs = [
            (clean_text, f"{meta['title']}. {context}")
            for meta, _, context in unique_papers
        ]
        reranker_scores = reranker.predict(pairs).tolist()
        ranked = sorted(
            zip(unique_papers, reranker_scores),
            key=lambda x: x[1],
            reverse=True,
        )
        ordered = [item for item, _ in ranked]
        scores_norm = [round(1 / (1 + math.exp(-s)), 4) for _, s in ranked]
    else:
        ordered = unique_papers
        scores_norm = [round(1 - dist, 4) for _, dist, _ in unique_papers]

    # Filter to workspace if provided
    if workspace_path:
        filtered = [
            (paper, score)
            for paper, score in zip(ordered, scores_norm)
            if paper[0].get("file_path", "").startswith(workspace_path)
        ]
        # Fall back to all results if workspace has nothing indexed yet
        if filtered:
            ordered = [p for p, _ in filtered]
            scores_norm = [s for _, s in filtered]

    citations: list[CitationResult] = []
    for (meta, _, _), score in zip(ordered[:n], scores_norm[:n]):
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

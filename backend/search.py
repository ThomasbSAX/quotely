"""
Two-stage retrieval: dense bi-encoder over the store, then a multilingual cross-encoder.

N rows, d dim, n results, P = n·POOL_PER_RESULT candidate chunks, R = RERANK_TOP papers:
  query embedding  O(L²) (one forward pass)
  dense scan       O(N·d) + O(N) partial selection
  grouping         O(P)
  reranking        O(R · L²) cross-encoder passes, L ≤ RERANK_MAX_WORDS
"""
from __future__ import annotations

import math
import re

import config
import embeddings as embed
from indexer import store
from models import CitationResult

_LATEX_RULES = [
    (re.compile(r"\\(?:textbf|textit|emph|underline|text)\{([^}]+)\}"), r"\1"),
    (re.compile(r"\\(?:cite[tp]?|ref|eqref|label|footnote)\{[^}]*\}"), ""),
    (re.compile(r"\\(?:begin|end)\{[^}]+\}"), ""),
    (re.compile(r"\\[a-zA-Z]+\*?\{[^}]*\}"), ""),
    (re.compile(r"\\[a-zA-Z]+\*?"), ""),
    (re.compile(r"\[@[^\]]*\]"), ""),
    (re.compile(r"\s+"), " "),
]


def clean_query(text: str) -> str:
    for pattern, repl in _LATEX_RULES:
        text = pattern.sub(repl, text)
    return text.strip()


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def scopes(workspace_path: str) -> tuple[str, ...]:
    return (workspace_path, str(config.DATA_PATH.resolve())) if workspace_path else ()


def candidates(query: str, n: int, workspace_path: str = "") -> list[tuple[dict, float, str]]:
    col = store()
    hits = col.query(embed.embed_query(query), n * config.POOL_PER_RESULT, scopes(workspace_path))
    papers: dict[str, list[tuple[int, float]]] = {}
    for i, sim in hits:
        group = papers.setdefault(col.metadatas[i]["bibtex_key"], [])
        if len(group) < config.CHUNKS_PER_PAPER:
            group.append((i, sim))
    out = []
    for group in papers.values():
        first, sim = group[0]
        context = " [...] ".join(col.documents[i] for i, _ in group)
        out.append((col.metadatas[first], sim, context))
    return out


def search(text: str, n: int = 5, workspace_path: str = "", rerank: bool = True) -> list[CitationResult]:
    query = clean_query(text)
    if not query or store().count() == 0:
        return []
    ranked = candidates(query, n, workspace_path)
    scores = [max(0.0, s) for _, s, _ in ranked]
    head = ranked[: config.RERANK_TOP]
    raw = embed.rerank(query, [f"{m['title']}. {_truncate(c, config.RERANK_MAX_WORDS)}" for m, _, c in head]) if rerank else None
    if raw is not None:
        order = sorted(range(len(head)), key=lambda j: -raw[j])
        ranked = [head[j] for j in order] + ranked[len(head):]
        scores = [_sigmoid(raw[j]) for j in order] + [0.0] * (len(ranked) - len(head))
    return [
        CitationResult(
            bibtex_key=m["bibtex_key"],
            title=m["title"],
            authors=m["authors"],
            year=m["year"],
            score=round(score, 4),
            bibtex_entry=m["bibtex_entry"],
            file_path=m["file_path"],
        )
        for (m, _, _), score in zip(ranked[:n], scores[:n])
    ]

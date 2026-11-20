"""
Retrieval and writing assistance.

Hybrid ranking: dense top-P passages (e5) fused with BM25 on that pool by reciprocal rank fusion,
then grouped per document; an optional cross-encoder pass reorders the head ("precise" mode).

N rows, d dim, P = DENSE_POOL, T tokens per passage, Q sentences analysed, R = RERANK_TOP.
  search   O(L²) query + O(N·d) + O(P·T) + O(P log P)            [+ O(R·L²) if precise]
  assist   sentences about the document's own structure ("the next section", "Table 3") are skipped, and a
           sentence must be closer to its passage than to the hollow academic phrases of
           resources/generic_sentences.txt (G of them, + O(Q·G·d)) minus ASSIST_GENERIC_MARGIN;
           O(Q·L²) queries (memoised) + O(Q·N·d) as one matmul + O(Q·P·T)
           + per kept suggestion, the contradiction check: O(S·L²) for the S sentences of its passage
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import config
import contradiction
import embeddings as embed
from indexer import store
from ingest import _SENT_RE

_LATEX_RULES = [
    (re.compile(r"(?<![\\\d])(?<!\d )%.*$", re.M), ""),
    (re.compile(r"\\%"), "%"),
    (re.compile(r"\\(?:textbf|textit|emph|underline|text|textsc)\{([^}]+)\}"), r"\1"),
    (re.compile(r"\\(?:[a-z]*cite[a-z]*|ref|eqref|autoref|cref|label|footnote|url)\*?(?:\[[^\]]*\])*\{[^}]*\}"), ""),
    (re.compile(r"\\(?:begin|end)\{[^}]+\}"), ""),
    (re.compile(r"\\[a-zA-Z]+\*?\{[^}]*\}"), ""),
    (re.compile(r"\\[a-zA-Z]+\*?"), ""),
    (re.compile(r"\[@[^\]]*\]|<!--.*?-->"), ""),
    (re.compile(r"[{}$]"), ""),
    (re.compile(r"\s+"), " "),
]
_SELF_REF_RE = re.compile(
    r"\b(?:the rest of (?:this|the) (?:paper|article|chapter|thesis)|"
    r"this (?:paper|article|chapter|section|thesis) (?:is organi[sz]ed|describes|presents|is structured)|"
    r"(?:in|see) (?:the )?(?:following|next|previous) (?:section|chapter)|"
    r"(?:summari[sz]ed|shown|reported|listed|displayed|presented) in (?:table|figure|fig\.|section|appendix)|"
    r"le reste de (?:cet|ce|cette) (?:article|chapitre|thèse|mémoire)|"
    r"(?:cet|ce|cette) (?:article|chapitre|section|thèse|mémoire) (?:est organisée?|présente|décrit|s'organise)|"
    r"(?:section|chapitre|partie) (?:suivante?|précédente?)|organisée? (?:comme suit|de la manière suivante)|"
    r"(?:présentée?s? |résumée?s? )?dans (?:le|la) (?:tableau|figure|section|annexe)\s*\d|"
    r"(?:table|figure|fig\.|section|chapter|appendix|tableau|chapitre|annexe)\s*~?\s*(?:\d|\\(?:auto|c)?ref))",
    re.I)
_CITED_RE = re.compile(r"\\[a-z]*cite[a-z]*\*?(?:\[[^\]]*\])*\{[^}]*\}|\[@[^\]]+\]")
_CLAIM_RE = re.compile(
    r"\b(\d+([.,]\d+)?\s?%|\d{4}|show(s|n|ed)?|demonstrat\w*|found|find|suggest\w*|report\w*|according|evidence|"
    r"studies|study|research\w*|previous|prior|known|propos\w*|introduc\w*|state[- ]of[- ]the[- ]art|outperform\w*|"
    r"montr\w*|démontr\w*|selon|d'après|étude\w*|recherche\w*|travaux|résultat\w*|propos\w*|introdui\w*|"
    r"connu\w*|établi\w*|observ\w*|significati\w*|majorit\w*|plupart|souvent|increas\w*|decreas\w*|augment\w*|diminu\w*)\b",
    re.I,
)


_GENERIC_FILE = Path(__file__).resolve().parent / "resources" / "generic_sentences.txt"
_generic: np.ndarray | None = None


def generic_vectors() -> np.ndarray:
    """Embeddings of hollow academic sentences (FR/EN); a sentence closer to them than to any passage is not a claim."""
    global _generic
    if _generic is None:
        lines = [l.strip() for l in _GENERIC_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        _generic = embed.embed_queries(lines)
    return _generic


def clean_query(text: str) -> str:
    for pattern, repl in _LATEX_RULES:
        text = pattern.sub(repl, text)
    return text.strip()


def scopes(workspace_path: str) -> tuple[str, ...]:
    return (workspace_path, str(config.DATA_PATH.resolve())) if workspace_path else ()


def confidence(sim: float) -> float:
    return round(min(1.0, max(0.0, (sim - 0.70) / 0.20)), 4)


@dataclass
class Hit:
    path: str
    score: float
    sim: float
    rows: list[int] = field(default_factory=list)


def _fuse(query: str, pool: list[tuple[int, float]]) -> list[Hit]:
    col = store()
    rows = [i for i, _ in pool]
    bm = col.bm25(query, rows)
    lex_rank = {rows[j]: r for r, j in enumerate(sorted(range(len(rows)), key=lambda j: -bm[j])) if bm[j] > 0}
    hits: dict[str, Hit] = {}
    fused = []
    for rank, (i, sim) in enumerate(pool):
        s = 1 / (config.RRF_K + rank) + (1 / (config.RRF_K + lex_rank[i]) if i in lex_rank else 0.0)
        fused.append((s, i, sim))
    for s, i, sim in sorted(fused, reverse=True):
        path = col.rows[i]["path"]
        hit = hits.setdefault(path, Hit(path, s, sim))
        hit.sim = max(hit.sim, sim)
        if len(hit.rows) < config.CHUNKS_PER_PAPER:
            hit.rows.append(i)
    return sorted(hits.values(), key=lambda h: -h.score)


def _passage(i: int) -> dict:
    col = store()
    row = col.rows[i]
    return {"text": col.texts[i], "page": row.get("page"), "section": row.get("section", ""), "title_row": row["chunk"] < 0}


def _best_passage(hit: Hit) -> dict:
    content = [i for i in hit.rows if store().rows[i]["chunk"] >= 0]
    return _passage(content[0] if content else hit.rows[0])


def _describe(hit: Hit, score: float) -> dict:
    doc = store().docs[hit.path]
    return {
        "bibtex_key": doc["bibtex_key"], "title": doc["title"], "authors": doc["authors"], "year": doc["year"],
        "doi": doc.get("doi", ""), "bibtex_entry": doc["bibtex_entry"], "file_path": hit.path,
        "score": round(score, 4), "confidence": confidence(hit.sim), "passage": _best_passage(hit),
    }


def search(text: str, n: int = 5, workspace_path: str = "", precise: bool | None = None) -> list[dict]:
    query = clean_query(text)
    col = store()
    if not query or col.count() == 0:
        return []
    pool = col.dense_top(embed.embed_queries([query]), config.DENSE_POOL, scopes(workspace_path))[0]
    hits = _fuse(query, pool)
    scores = [confidence(h.sim) for h in hits]
    if config.PRECISION_MODE if precise is None else precise:
        head = hits[: config.RERANK_TOP]
        docs = [f"{col.docs[h.path]['title']}. " + " ".join(" ".join(col.texts[i] for i in h.rows).split()[: config.RERANK_MAX_WORDS]) for h in head]
        raw = embed.rerank(query, docs)
        if raw is not None:
            order = sorted(range(len(head)), key=lambda j: -raw[j])
            hits = [head[j] for j in order] + hits[len(head):]
            scores = [1 / (1 + math.exp(-raw[j])) for j in order] + scores[len(head):]
    return [_describe(h, s) for h, s in zip(hits[:n], scores[:n])]


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    out, start = [], 0
    for m in list(_SENT_RE.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        chunk = text[start:end]
        stripped = chunk.strip()
        if stripped:
            offset = start + chunk.index(stripped)
            out.append((offset, offset + len(stripped), stripped))
        start = m.end() if m else end
    return out


def assist(text: str, workspace_path: str = "", cited_keys: list[str] | None = None, threshold: float | None = None,
           document_path: str = "") -> list[dict]:
    """Sentences of `text` that a source in the library supports, with the best passages.

    `document_path` is the document being written: when it is itself indexed, it is never its own source."""
    col = store()
    if col.count() == 0:
        return []
    threshold = config.ASSIST_THRESHOLD if threshold is None else threshold
    cited = set(cited_keys or [])
    candidates = []
    for start, end, raw in split_sentences(text):
        query = clean_query(raw)
        if len(query.split()) < config.ASSIST_MIN_WORDS or _SELF_REF_RE.search(raw):
            continue
        candidates.append((start, end, raw, query))
    candidates = candidates[-config.ASSIST_MAX_SENTENCES:]
    if not candidates:
        return []
    vecs = embed.embed_queries([c[3] for c in candidates])
    pools = col.dense_top(vecs, config.DENSE_POOL // 2, scopes(workspace_path))
    if document_path:
        own = str(Path(document_path).resolve())
        pools = [[(i, sim) for i, sim in pool if col.rows[i]["path"] != own] for pool in pools]
    out = []
    generics = (vecs @ generic_vectors().T).max(axis=1)
    for (start, end, raw, query), vec, pool, generic in zip(candidates, vecs, pools, generics):
        if not pool:
            continue
        claim = bool(_CLAIM_RE.search(query))
        has_citation = bool(_CITED_RE.search(raw))
        gate = max(threshold + (0.03 if has_citation else 0.0), generic - config.ASSIST_GENERIC_MARGIN)
        hits = [h for h in _fuse(query, pool)[:3] if h.sim >= gate]
        if not hits:
            continue
        best = max(h.sim for h in hits)
        hits = [h for h in hits if h.sim >= best - config.ASSIST_SECONDARY_MARGIN]
        suggestions = []
        for h in hits:
            d = _describe(h, confidence(h.sim))
            d["kind"] = "recall" if d["bibtex_key"] in cited else "cite"
            cue = contradiction.check(query, vec, d["passage"]["text"]) if config.ASSIST_CONTRADICTION else ""
            d["contradiction"] = cue
            if cue:
                d["confidence"] = round(d["confidence"] * 0.5, 4)
            suggestions.append(d)
        suggestions.sort(key=lambda d: (bool(d["contradiction"]), -d["confidence"]))
        out.append({
            "start": start, "end": end, "sentence": raw, "claim": claim,
            "already_cited": has_citation, "suggestions": suggestions,
        })
    return out

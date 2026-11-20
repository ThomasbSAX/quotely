"""
Light consistency check between a written sentence and the passage that seems to support it.

Embeddings place "X increases Y" and "X does not increase Y" side by side, so a sentence that
contradicts its source is matched as strongly as a faithful one. Within the passage, the sentence
closest to the query is compared with it on three cues (French and English):
  - verbal negation present on one side only ("not", "never", "ne … pas / jamais"; idioms such as
    "without bias" or "not only" do not count)
  - opposite direction words (increase / decrease, more / fewer, hausse / baisse…), unless the
    next two closest sentences go the same way as the query
  - percentages that do not match (within 1 %), or shared numbers in a different order (from 21 to 32
    vs from 32 to 21); skipped when the source sentence is a table row (more than 8 numbers)
A hit only lowers the confidence and asks the writer to check; it never hides the source.

S sentences in the passage: one batched embedding of S short texts (memoised) + O(S·d) + O(T) regex.
"""
from __future__ import annotations

import re

import numpy as np

import embeddings as embed
from ingest import sentences

_NEG_RE = re.compile(
    r"\b(?:not(?!\s+(?:only|just|merely|necessarily))|never|neither|nor|cannot|impossible|unable|\w+n['’]t|"
    r"no(?=\s+(?:evidence|sign|effect|difference|significant|corresponding|association|impact|change)\b)|"
    r"pas(?!\s+seulement)|jamais|aucun\w*|rien|nullement|guère)\b", re.I)
_UP_RE = re.compile(
    r"\b(?:increas\w*|rais(?:e|es|ed|ing)|rises?|rose|grow(?:s|n|th)?|grew|higher|greater|larger|more|outperform\w*|"
    r"better|improv\w*|augment\w*|hausse\w*|accro\w*|croissan\w*|supérieur\w*|meilleur\w*|amplif\w*)\b", re.I)
_DOWN_RE = re.compile(
    r"\b(?:decreas\w*|lower(?:s|ed|ing)?|less|fewer|fall(?:s|en|ing)?|fell|declin\w*|reduc\w*|drops?|dropped|smaller|"
    r"worse|underperform\w*|suppress\w*|diminu\w*|baisse\w*|moins|inférieur\w*|réduit\w*|réduction\w*|recul\w*|chut\w*|"
    r"déclin\w*|pire)\b", re.I)
_TABLE_NUMBERS = 8
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(?:%|percent\b|pour\s?cent\b)", re.I)
_NUM_RE = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?(?![\w])")


def _num(x: str) -> str:
    x = re.sub(r"[ \u00a0\u202f]", "", x)
    return x.replace(",", ".").rstrip("0").rstrip(".") if "." in x.replace(",", ".") else x


def _closest(query_vec: np.ndarray, passage: str, k: int = 3) -> list[str]:
    sents = [s for s in sentences(passage) if len(s.split()) >= 4] or [passage]
    sims = embed.embed_queries(sents) @ query_vec
    return [sents[i] for i in np.argsort(-sims)[:k]]


def _direction(text: str) -> int:
    up, down = bool(_UP_RE.search(text)), bool(_DOWN_RE.search(text))
    return (1 if up else 0) - (1 if down else 0)


def _close(a: str, b: str) -> bool:
    x, y = float(a), float(b)
    return abs(x - y) <= max(0.5, 0.01 * max(abs(x), abs(y)))


def _numbers_disagree(query: str, source: str) -> bool:
    sn = [_num(x) for x in _NUM_RE.findall(source)]
    if len(sn) > _TABLE_NUMBERS:
        return False
    qp = [_num(x) for x in _PCT_RE.findall(query)]
    sp = [_num(x) for x in _PCT_RE.findall(source)]
    if qp and sp and not any(_close(x, y) for x in qp for y in sp):
        return True
    qn = [_num(x) for x in _NUM_RE.findall(query)]
    shared = [x for x in dict.fromkeys(qn) if x in sn]
    return len(shared) >= 2 and shared != [x for x in dict.fromkeys(sn) if x in shared]


def check(query: str, query_vec: np.ndarray, passage: str) -> str:
    """Empty string when nothing looks off, otherwise the cue that fired."""
    source, *others = _closest(query_vec, passage)
    if bool(_NEG_RE.search(query)) != bool(_NEG_RE.search(source)):
        return "negation"
    dq = _direction(query)
    ds = [_direction(t) for t in [source, *others]]
    if dq and ds[0] and ds[0] != dq and dq not in ds:
        return "direction"
    if _numbers_disagree(query, source):
        return "numbers"
    return ""

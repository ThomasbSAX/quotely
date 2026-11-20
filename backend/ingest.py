"""
Ingestion: extraction → metadata (with the user's .bib when it matches, else optionally Crossref / arXiv) → sentence-aware
passages with page and section → embeddings → store.

W words, S sentences, C passages (≈ W / CHUNK_WORDS), L ≤ 512 tokens per passage.
  extraction   O(W) (+ OCR only for pages without text)
  passages     O(S)
  embedding    O(C · L²), batched
  store        O(C·d) for a new file, O(N·d) when replacing a changed one
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import config
import embeddings as embed
import online_metadata
from bibtex import BibLibrary, format_entry, make_key
from extract import _infer_author_from_path, _infer_author_from_text, extract
from vector_store import VectorStore

_HEADING_RE = re.compile(r"^\s*(?:#{1,4}\s+(.+)|==\s*(.+?)\s*==)\s*$")
_SENT_RE = re.compile(r"(?<=[.!?…])[\"»”)]*\s+(?=[«\"“(\[]?[A-ZÀ-Ý0-9])")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def is_supported(path: Path) -> bool:
    return Path(path).suffix.lower() in config.SUPPORTED_EXTENSIONS and not _in_db(path)


def _in_db(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(config.DB_PATH.resolve())
        return True
    except ValueError:
        return False


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(re.sub(r"\s+", " ", text)) if s.strip()]


def _units(pages: list[tuple[int | None, str]]):
    section = ""
    for page, text in pages:
        para: list[str] = []
        for line in text.splitlines() + [""]:
            m = _HEADING_RE.match(line)
            if m or not line.strip():
                if para:
                    for s in sentences(" ".join(para)):
                        yield page, section, s
                    para = []
                if m:
                    section = (m.group(1) or m.group(2)).strip()[:120]
                continue
            para.append(line.strip())


def passages(pages: list[tuple[int | None, str]]) -> list[dict]:
    out: list[dict] = []
    cur: list[str] = []
    words = 0
    page0 = section0 = None

    def flush(keep_last: bool, final: bool = False) -> None:
        nonlocal cur, words, page0
        if cur and (words >= config.MIN_CHUNK_WORDS or (final and words >= 3)):
            out.append({"text": " ".join(cur), "page": page0, "section": section0})
        cur = cur[-1:] if keep_last and len(cur) > 1 else []
        words = sum(len(s.split()) for s in cur)
        page0 = page0 if cur else None

    for page, section, sent in _units(pages):
        toks = sent.split()
        if len(toks) > config.CHUNK_WORDS:
            flush(False)
            for i in range(0, len(toks), config.CHUNK_WORDS):
                out.append({"text": " ".join(toks[i:i + config.CHUNK_WORDS]), "page": page, "section": section})
            continue
        if section != section0 or words + len(toks) > config.CHUNK_WORDS:
            flush(section == section0)
        if not cur:
            page0, section0 = page, section
        cur.append(sent)
        words += len(toks)
    flush(False, final=True)
    return out


def _unique_key(store: VectorStore, base: str, path: str) -> str:
    key, n = base, 0
    while store.key_owner(key) not in (None, path):
        n += 1
        key = base + chr(ord("a") + (n - 1) % 26) * (1 + (n - 1) // 26)
    return key


def _clean_title(title: str, path: Path) -> str:
    title = re.sub(r"^[#*\s]+|[*\s]+$", "", title or "")
    return re.sub(r"\s+", " ", title) or path.stem


def ingest_file(path: Path, store: VectorStore, sha: str | None = None, bib: BibLibrary | None = None) -> dict | None:
    """Index `path`; returns None when this exact content is already indexed for this path."""
    path = Path(path).resolve()
    spath = str(path)
    sha = sha or file_hash(path)
    previous = store.doc(spath)
    if previous and previous["file_hash"] == sha:
        return None

    pages, meta = extract(path)
    chunks = passages(pages)
    if not chunks:
        raise ValueError("No text extracted from document.")

    meta["title"] = _clean_title(meta.get("title", ""), path)
    meta["authors"] = meta.get("authors") or _infer_author_from_path(path) or _infer_author_from_text(" ".join(t for _, t in pages[:1]))
    entry = bib.match(meta) if bib else None
    if not entry and config.ONLINE_METADATA:
        meta.update(online_metadata.lookup(meta))
        entry = bib.match(meta) if bib else None
    if entry:
        key = entry["key"]
        meta.update(title=entry.get("title", meta["title"]).replace("{", "").replace("}", ""),
                    authors=entry.get("author", meta["authors"]), year=entry.get("year", meta["year"]),
                    doi=entry.get("doi", meta.get("doi", "")))
        bibtex_entry = entry["raw"]
    else:
        key = (previous or {}).get("bibtex_key") or store.stale_keys.get(spath)
        key = key if key and store.key_owner(key) in (None, spath) else _unique_key(store, make_key(meta["authors"], meta["year"], meta["title"]), spath)
        bibtex_entry = format_entry(key, meta)

    head = meta["title"] + (f" — {meta['authors']}" if meta["authors"] else "")
    texts = [head] + [c["text"] for c in chunks]
    rows = [{"chunk": -1, "page": None, "section": ""}] + [
        {"chunk": i, "page": c["page"], "section": c["section"]} for i, c in enumerate(chunks)]
    vectors = embed.embed_passages([head] + [
        f"{meta['title']}. {c['section'] + '. ' if c['section'] else ''}{c['text']}" for c in chunks])

    doc = {
        "bibtex_key": key, "title": meta["title"], "authors": meta["authors"], "year": meta["year"],
        "doi": meta.get("doi", ""), "arxiv": meta.get("arxiv", ""), "file_hash": sha,
        "bibtex_entry": bibtex_entry, "from_bib": bool(entry), "pages": max((p or 0) for p, _ in pages),
    }
    with store.lock:
        if store.key_owner(key) not in (None, spath):
            key = _unique_key(store, key, spath)
            doc["bibtex_key"] = key
            doc["bibtex_entry"] = format_entry(key, meta)
        store.add_document(spath, doc, vectors, texts, rows)
    print(f"[Quotely] Indexed '{meta['title']}' ({len(texts)} passages) → {key}")
    return {"bibtex_key": key, "title": meta["title"], "chunks_indexed": len(texts)}


def remove_file(path: Path, store: VectorStore) -> bool:
    return store.remove_document(str(Path(path).resolve()))

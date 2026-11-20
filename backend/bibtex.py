"""
BibTeX: parsing of the user's own .bib files, key generation and entry formatting.

Parsing is a single brace-matching scan, O(B) for a file of B bytes.
Matching a document against the loaded entries is O(1) by DOI / arXiv id / normalised title.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_STOP = {"a", "an", "the", "de", "du", "des", "le", "la", "les", "et", "en", "un", "une",
         "of", "in", "on", "to", "for", "and", "with", "sur", "pour", "dans", "par", "cours"}


def ascii_fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", ascii_fold(title).lower().replace("{", "").replace("}", "")).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", ascii_fold(text))


def first_lastname(authors: str) -> str:
    first = re.split(r"\s+and\s+|;|,(?=\s*[A-Z][a-z]+\s+[A-Z])", authors.strip())[0].strip() if authors else ""
    if not first:
        return "Anon"
    if "@" in first:
        parts = re.split(r"[.\-_]", first.split("@")[0])
        return _slug(parts[-1]).capitalize() or "Anon"
    if "," in first:
        return _slug(first.split(",")[0]).capitalize() or "Anon"
    parts = [p for p in first.split() if _slug(p)]
    return _slug(parts[-1]).capitalize() if parts else "Anon"


def make_key(authors: str, year: str, title: str) -> str:
    word = next((w for w in (_slug(x) for x in re.split(r"[\s\-–—:]+", title))
                 if len(w) > 2 and w.lower() not in _STOP), "Untitled")
    y = year if re.fullmatch(r"\d{4}", year or "") else "nd"
    return f"{first_lastname(authors)}{y}{word.capitalize()}"


def _escape(value: str) -> str:
    value = value.replace("\\", "\\textbackslash{}")
    return re.sub(r"([&%$#_])", r"\\\1", value).replace("{", "").replace("}", "")


def bib_authors(authors: str) -> str:
    if not authors:
        return ""
    if " and " in authors:
        return authors
    parts = [p.strip() for p in re.split(r";|,\s*(?=[A-ZÀ-Ý][^,]*\s)", authors) if p.strip()]
    return " and ".join(parts)


def format_entry(key: str, meta: dict) -> str:
    fields = [("title", "{" + _escape(meta.get("title", "")) + "}"),
              ("author", _escape(bib_authors(meta.get("authors", "")))),
              ("year", meta.get("year", "") if meta.get("year") != "n.d." else "")]
    if meta.get("journal"):
        fields.append(("journal", _escape(meta["journal"])))
    if meta.get("doi"):
        fields.append(("doi", meta["doi"]))
    if meta.get("arxiv"):
        fields += [("eprint", meta["arxiv"]), ("archiveprefix", "arXiv")]
    kind = "article" if meta.get("doi") or meta.get("journal") else "misc"
    body = ",\n".join(f"  {name:<13}= {{{value}}}" for name, value in fields if value)
    return f"@{kind}{{{key},\n{body}\n}}"


def _fields(body: str) -> dict:
    out, i, n = {}, 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z_\-]+)\s*=\s*").match(body, i)
        if not m:
            break
        name, i = m.group(1).lower(), m.end()
        if i < n and body[i] in "{\"":
            close, depth, j = ("}" if body[i] == "{" else "\""), 0, i
            while j < n:
                c = body[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                if (close == "}" and depth == 0 and c == "}") or (close == "\"" and c == "\"" and j > i and depth == 0):
                    break
                j += 1
            out[name] = re.sub(r"\s+", " ", body[i + 1:j]).strip()
            i = j + 1
        else:
            m2 = re.compile(r"[^,]+").match(body, i)
            out[name] = m2.group(0).strip() if m2 else ""
            i = m2.end() if m2 else n
    return out


def parse(text: str) -> list[dict]:
    entries, i = [], 0
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.start() < i or m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        fields = _fields(text[m.end():j - 1])
        entries.append({"key": m.group(2), "type": m.group(1).lower(), "raw": text[m.start():j].strip(), **fields})
        i = j
    return entries


class BibLibrary:
    def __init__(self) -> None:
        self.by_doi: dict[str, dict] = {}
        self.by_arxiv: dict[str, dict] = {}
        self.by_title: dict[str, dict] = {}

    def load(self, path: Path) -> int:
        entries = parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
        for e in entries:
            if e.get("doi"):
                self.by_doi[e["doi"].lower()] = e
            if e.get("eprint"):
                self.by_arxiv[re.sub(r"v\d+$", "", e["eprint"])] = e
            if e.get("title"):
                self.by_title[norm_title(e["title"])] = e
        return len(entries)

    def match(self, meta: dict) -> dict | None:
        return (self.by_doi.get((meta.get("doi") or "").lower())
                or self.by_arxiv.get(meta.get("arxiv") or "")
                or self.by_title.get(norm_title(meta.get("title") or "")))

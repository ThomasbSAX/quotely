"""
Optional metadata enrichment from Crossref (DOI) and arXiv (arXiv id), off by default
(QUOTELY_ONLINE_METADATA=1, set by the quotely.onlineMetadata setting). Only the identifier is
sent. Answers, including "not found", are cached in data/db/online_metadata.json, so each
identifier costs one HTTP request ever; a network failure is not cached and is retried later.

Lookup: O(1) when cached, otherwise one request (timeout TIMEOUT s) + an O(C) atomic rewrite of
the cache for C cached identifiers.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config
from extract import _cap_authors

TIMEOUT = 8
_UA = "Quotely/2 (https://github.com/ThomasbSAX/quotely)"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
_lock = threading.Lock()
_cache: dict[str, dict] | None = None


def _path():
    return config.DB_PATH / "online_metadata.json"


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_path().read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _store(key: str, value: dict) -> None:
    cache = _load()
    cache[key] = value
    _path().parent.mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False))
    os.replace(tmp, _path())


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _crossref(doi: str) -> dict:
    try:
        msg = json.loads(_get("https://api.crossref.org/works/" + urllib.parse.quote(doi)))["message"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise
    authors = "; ".join(" ".join(p for p in (a.get("given"), a.get("family")) if p) for a in msg.get("author", []))
    year = next((str(msg[k]["date-parts"][0][0]) for k in ("published-print", "published-online", "issued")
                 if msg.get(k, {}).get("date-parts", [[None]])[0][0]), "")
    return {k: v for k, v in {
        "title": " ".join((msg.get("title") or [""])[0].split()), "authors": authors, "year": year,
        "journal": (msg.get("container-title") or [""])[0],
    }.items() if v}


def _arxiv(arxiv_id: str) -> dict:
    root = ET.fromstring(_get("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)))
    entry = root.find("a:entry", _ATOM)
    if entry is None or entry.find("a:title", _ATOM) is None:
        return {}
    doi = entry.find("x:doi", _ATOM)
    return {k: v for k, v in {
        "title": " ".join(entry.find("a:title", _ATOM).text.split()),
        "authors": "; ".join(a.find("a:name", _ATOM).text for a in entry.findall("a:author", _ATOM)),
        "year": (entry.find("a:published", _ATOM).text or "")[:4],
        "doi": doi.text.strip() if doi is not None else "",
    }.items() if v}


def lookup(meta: dict) -> dict:
    """Fields found online for this document's DOI or arXiv id ({} when disabled or unknown)."""
    if not config.ONLINE_METADATA:
        return {}
    doi = (meta.get("doi") or "").lower()
    if doi and not doi.startswith("10.48550/"):
        key, fetch = "doi:" + doi, lambda: _crossref(doi)
    elif meta.get("arxiv") or doi:
        arxiv_id = meta.get("arxiv") or re.sub(r"^10\.48550/arxiv\.", "", doi)
        key, fetch = "arxiv:" + arxiv_id, lambda: _arxiv(arxiv_id)
    else:
        return {}
    with _lock:
        if key in _load():
            return _load()[key]
    try:
        found = fetch()
    except Exception as e:
        print(f"[Quotely] Online metadata unavailable for {key}: {e}")
        return {}
    if found.get("authors"):
        found["authors"] = _cap_authors(found["authors"])
    with _lock:
        _store(key, found)
    return found

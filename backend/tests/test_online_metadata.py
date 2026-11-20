import json

import config
import indexer
import online_metadata
from conftest import write
from ingest import ingest_file

CROSSREF = {"message": {"title": ["Les ouvriers en France"], "author": [{"given": "Jeanne", "family": "Dupont"}],
                        "issued": {"date-parts": [[2019, 3]]}, "container-title": ["Revue de test"]}}


def test_disabled_by_default(home, monkeypatch):
    monkeypatch.setattr(online_metadata, "_get", lambda url: (_ for _ in ()).throw(AssertionError("network")))
    assert online_metadata.lookup({"doi": "10.1/x"}) == {}


def test_crossref_lookup_is_cached_and_used(home, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "ONLINE_METADATA", True)
    monkeypatch.setattr(online_metadata, "_cache", None)
    monkeypatch.setattr(online_metadata, "_get", lambda url: calls.append(url) or json.dumps(CROSSREF).encode())
    doc = write(config.PAPERS_PATH / "o.md", "Titre provisoire\n\ndoi: 10.4000/test.1\n\n" + "La part des ouvriers a baissé depuis 1982. " * 5)
    ingest_file(doc, indexer.store())
    d = indexer.store().doc(str(doc.resolve()))
    assert d["title"] == "Les ouvriers en France" and d["year"] == "2019" and d["bibtex_key"] == "Dupont2019Ouvriers"
    assert "journal" in d["bibtex_entry"] and "Revue de test" in d["bibtex_entry"]
    online_metadata._cache = None
    assert online_metadata.lookup({"doi": "10.4000/TEST.1"})["authors"] == "Jeanne Dupont"
    assert len(calls) == 1 and (config.DB_PATH / "online_metadata.json").exists()


def test_network_failure_is_not_cached(home, monkeypatch):
    monkeypatch.setattr(config, "ONLINE_METADATA", True)
    monkeypatch.setattr(online_metadata, "_cache", None)
    monkeypatch.setattr(online_metadata, "_get", lambda url: (_ for _ in ()).throw(OSError("offline")))
    assert online_metadata.lookup({"arxiv": "2611.00754"}) == {}
    assert "arxiv:2611.00754" not in online_metadata._load()

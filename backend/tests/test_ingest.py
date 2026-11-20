from conftest import write

import config
import indexer
from ingest import _chunk_markdown, _extract_year, ingest_file, remove_file
from search import clean_query, search

BODY = " ".join(f"mot{i}" for i in range(30))


def test_year_extraction():
    assert _extract_year("D:20210304 something 1999") == "2021"
    assert _extract_year("page 4000 then 1987") == "1987"
    assert _extract_year("no year") == "n.d."


def test_chunk_sizes_respect_limit():
    text = "# Intro\n" + " ".join(["w"] * 1000) + "\n# Short\n" + " ".join(["x"] * 30)
    chunks = _chunk_markdown(text)
    assert all(len(c.split()) <= config.CHUNK_WORDS + 5 for c in chunks)
    assert any("x x x" in c for c in chunks)


def test_bibtex_key_collision_gets_suffix(home):
    store = indexer.store()
    a = write(config.PAPERS_PATH / "a.md", f"# Deep learning survey\nby nobody 2020 {BODY} alpha")
    b = write(config.PAPERS_PATH / "b.md", f"# Deep learning survey\nby nobody 2020 {BODY} beta")
    ka = ingest_file(a, store)["bibtex_key"]
    kb = ingest_file(b, store)["bibtex_key"]
    assert ka != kb and kb.startswith(ka)
    assert store.key_owner(ka) == str(a.resolve())


def test_modified_file_replaces_old_chunks_and_keeps_key(home):
    store = indexer.store()
    p = write(config.PAPERS_PATH / "p.md", f"# Graph neural networks 2019\n{BODY} oldcontent")
    key = ingest_file(p, store)["bibtex_key"]
    assert ingest_file(p, store) is None
    write(p, f"# Graph neural networks 2019\n{BODY} newcontent")
    assert ingest_file(p, store)["bibtex_key"] == key
    docs = " ".join(store.documents)
    assert "newcontent" in docs and "oldcontent" not in docs
    remove_file(p, store)
    assert store.count() == 0


def test_workspace_scoping(home, tmp_path):
    ws, other = tmp_path / "ws", tmp_path / "other"
    store = indexer.store()
    ingest_file(write(other / "o.md", f"# Protein folding 2021\n{BODY} protein folding structure"), store)
    ingest_file(write(ws / "w.md", f"# Galaxy formation 2018\n{BODY} galaxy dark matter"), store)
    ingest_file(write(config.PAPERS_PATH / "g.md", f"# Shared library 2017\n{BODY} protein"), store)
    keys = {c.title for c in search("protein folding structure", 5, str(ws.resolve()))}
    assert "Protein folding 2021" not in keys and "Shared library 2017" in keys


def test_clean_query():
    assert clean_query(r"As shown \cite{X} in \textbf{deep} nets [@Y]") == "As shown in deep nets"


def test_reindex_resumes_and_swaps(home):
    for i in range(3):
        write(config.PAPERS_PATH / f"f{i}.md", f"# Title number {i} 2020\n{BODY} topic{i}")
    indexer.startup()
    assert indexer.store().count() > 0
    result = indexer.reindex()
    assert result["total"] == 3 and not config.STAGING_PATH.exists()
    assert indexer.store().path == config.INDEX_PATH


def test_interrupted_reindex_resumes_from_checkpoint(home, monkeypatch):
    import ingest as ingest_mod
    for i in range(4):
        write(config.PAPERS_PATH / f"f{i}.md", f"# Title number {i} 2020\n{BODY} topic{i}")
    real, calls = ingest_mod.ingest_file, []

    def flaky(path, store, sha=None):
        calls.append(path.name)
        if len(calls) == 3:
            raise KeyboardInterrupt
        return real(path, store, sha)

    monkeypatch.setattr(indexer, "ingest_file", flaky)
    try:
        indexer.reindex()
    except KeyboardInterrupt:
        pass
    assert config.STAGING_PATH.exists()
    monkeypatch.setattr(indexer, "ingest_file", real)
    calls.clear()
    monkeypatch.setattr(indexer, "ingest_file", lambda p, s, sha=None: calls.append(real(p, s, sha)) or calls[-1])
    indexer.reindex()
    assert sum(r is not None for r in calls) == 2 and not config.STAGING_PATH.exists()
    assert len({m["file_path"] for m in indexer.store().metadatas}) == 4

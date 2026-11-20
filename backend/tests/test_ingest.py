import config
import indexer
from conftest import write
from extract import _extract_year
from ingest import ingest_file, passages, remove_file
from search import assist, clean_query, search, split_sentences

FILLER = " ".join(f"mot{i}" for i in range(30))


def paper(title, body, year="2020"):
    return f"# {title}\nAuteur {year}\n\n## Introduction\n{FILLER}. {body}\n"


def test_year_extraction():
    assert _extract_year("D:20210304 something 1999") == "2021"
    assert _extract_year("page 4000 then 1987") == "1987"
    assert _extract_year("no year") == "n.d."


def test_passages_keep_pages_sections_and_size():
    long_sentence = " ".join(["w"] * 300) + "."
    pages = [(1, "# Intro\nFirst sentence is here with words. Second one follows right after it.\n"),
             (2, "# Methods\n" + long_sentence + "\nA final sentence that closes the methods section well.")]
    out = passages(pages)
    assert all(len(p["text"].split()) <= config.CHUNK_WORDS for p in out)
    assert out[0]["section"] == "Intro" and out[0]["page"] == 1
    assert {p["section"] for p in out} == {"Intro", "Methods"} and out[-1]["page"] == 2


def test_key_collision_gets_suffix(home):
    store = indexer.store()
    a = write(config.PAPERS_PATH / "a.md", paper("Deep learning survey", "alpha"))
    b = write(config.PAPERS_PATH / "b.md", paper("Deep learning survey", "beta"))
    ka = ingest_file(a, store)["bibtex_key"]
    kb = ingest_file(b, store)["bibtex_key"]
    assert ka != kb and kb.startswith(ka) and store.key_owner(ka) == str(a.resolve())


def test_modified_file_replaces_passages_and_keeps_key(home):
    store = indexer.store()
    p = write(config.PAPERS_PATH / "p.md", paper("Graph neural networks", "Old content sentence about graphs."))
    key = ingest_file(p, store)["bibtex_key"]
    assert ingest_file(p, store) is None
    write(p, paper("Graph networks renamed", "New content sentence about graphs."))
    assert ingest_file(p, store)["bibtex_key"] == key
    joined = " ".join(store.texts)
    assert "New content" in joined and "Old content" not in joined
    remove_file(p, store)
    assert store.count() == 0


def test_user_bib_key_is_reused(home):
    write(config.PAPERS_PATH / "refs.bib", "@article{Mine2019, title={Graph neural networks}, author={Ann Lee}, year={2019}}")
    write(config.PAPERS_PATH / "g.md", paper("Graph neural networks", "Graphs are everywhere in biology."))
    indexer.startup()
    d = next(iter(indexer.store().docs.values()))
    assert d["bibtex_key"] == "Mine2019" and d["from_bib"] and d["authors"] == "Ann Lee"


def test_keys_survive_model_change(home, monkeypatch):
    write(config.PAPERS_PATH / "k.md", paper("Stable keys matter", "Keys must not change between versions."))
    indexer.startup()
    key = next(iter(indexer.store().docs.values()))["bibtex_key"]
    import embeddings
    monkeypatch.setattr(embeddings, "model_id", lambda: "another-model")
    monkeypatch.setattr(indexer, "_store", None)
    indexer.startup()
    assert next(iter(indexer.store().docs.values()))["bibtex_key"] == key


def test_workspace_scoping(home, tmp_path):
    ws, other = tmp_path / "ws", tmp_path / "other"
    store = indexer.store()
    ingest_file(write(other / "o.md", paper("Protein folding", "Protein folding structure prediction.")), store)
    ingest_file(write(ws / "w.md", paper("Galaxy formation", "Galaxy dark matter halos.")), store)
    ingest_file(write(config.PAPERS_PATH / "g.md", paper("Shared library", "Protein folding is hard.")), store)
    titles = {c["title"] for c in search("protein folding structure", 5, str(ws.resolve()))}
    assert "Protein folding" not in titles and "Shared library" in titles


def test_search_returns_passage(home):
    store = indexer.store()
    ingest_file(write(config.PAPERS_PATH / "x.md", paper("Attention models", "Transformers rely on self attention layers for sequence modelling.")), store)
    hit = search("self attention transformers", 1)[0]
    assert "self attention" in hit["passage"]["text"] and hit["bibtex_key"]


def test_assist_flags_supported_sentences(home):
    store = indexer.store()
    ingest_file(write(config.PAPERS_PATH / "x.md", paper("Coral reefs", "Coral reef bleaching increased sharply with ocean warming events.")), store)
    text = "Ceci est une phrase banale. Coral reef bleaching increased sharply with ocean warming events worldwide."
    items = assist(text, threshold=0.45)
    assert len(items) == 1 and text[items[0]["start"]:items[0]["end"]] == items[0]["sentence"]
    assert items[0]["suggestions"][0]["kind"] == "cite"
    key = items[0]["suggestions"][0]["bibtex_key"]
    assert assist(text, cited_keys=[key], threshold=0.45)[0]["suggestions"][0]["kind"] == "recall"


def test_assist_never_cites_the_document_being_written(home):
    draft = write(config.PAPERS_PATH / "draft.md", paper("My draft", "Coral reef bleaching increased sharply with ocean warming events."))
    ingest_file(draft, indexer.store())
    text = "Coral reef bleaching increased sharply with ocean warming events worldwide."
    assert assist(text, threshold=0.45)
    assert assist(text, threshold=0.45, document_path=str(draft)) == []


def test_split_sentences_offsets():
    text = "  First one here. Second, longer one! Third?"
    for start, end, s in split_sentences(text):
        assert text[start:end] == s


def test_clean_query():
    assert clean_query(r"As shown \citep[p.~3]{X} in \textbf{deep} nets [@Y] % comment") == "As shown in deep nets"


def test_reindex_swaps_staging(home):
    for i in range(3):
        write(config.PAPERS_PATH / f"f{i}.md", paper(f"Title number {i}", f"Topic {i} sentence content here."))
    indexer.startup()
    result = indexer.reindex()
    assert result["total"] == 3 and not config.STAGING_PATH.exists()
    assert indexer.store().path == config.INDEX_PATH


def test_interrupted_reindex_resumes_from_checkpoint(home, monkeypatch):
    for i in range(4):
        write(config.PAPERS_PATH / f"f{i}.md", paper(f"Title number {i}", f"Topic {i} sentence content here."))
    real, calls = indexer.ingest_file, []

    def flaky(path, store, sha=None, bib=None):
        calls.append(path.name)
        if len(calls) == 3:
            raise KeyboardInterrupt
        return real(path, store, sha, bib)

    monkeypatch.setattr(indexer, "ingest_file", flaky)
    try:
        indexer.reindex()
    except KeyboardInterrupt:
        pass
    assert config.STAGING_PATH.exists()
    results = []
    monkeypatch.setattr(indexer, "ingest_file", lambda p, s, sha=None, bib=None: results.append(real(p, s, sha, bib)) or results[-1])
    indexer.reindex()
    assert sum(r is not None for r in results) == 2 and not config.STAGING_PATH.exists()
    assert len(indexer.store().docs) == 4


def test_assist_skips_structure_and_hollow_sentences(home, monkeypatch):
    import search
    ingest_file(write(config.PAPERS_PATH / "x.md", paper("Coral reefs", "Coral reef bleaching increased sharply with ocean warming events.")), indexer.store())
    assert assist("As shown in Table 3, coral reef bleaching increased sharply with ocean warming events.", threshold=0.45) == []
    text = "Coral reef bleaching increased sharply with ocean warming events worldwide."
    monkeypatch.setattr(search, "_generic", search.embed.embed_queries([text]))
    assert assist(text, threshold=0.45) == []

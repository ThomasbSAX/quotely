from bibtex import BibLibrary, format_entry, make_key, parse

BIB = r"""
@article{Smith2020Deep,
  title = {Deep {Learning} for {NLP}},
  author = "Smith, John and Doe, Jane",
  year = 2020,
  doi = {10.1000/xyz.123}
}
@comment{ignored}
@misc{Other, title={A, B}, eprint={2101.00001v2}}
"""


def test_parse_fields_and_nested_braces():
    entries = parse(BIB)
    assert [e["key"] for e in entries] == ["Smith2020Deep", "Other"]
    assert entries[0]["author"] == "Smith, John and Doe, Jane" and entries[0]["year"] == "2020"
    assert entries[1]["title"] == "A, B"


def test_library_matches_by_doi_arxiv_title(tmp_path):
    p = tmp_path / "refs.bib"
    p.write_text(BIB)
    lib = BibLibrary()
    lib.load(p)
    assert lib.match({"doi": "10.1000/XYZ.123"})["key"] == "Smith2020Deep"
    assert lib.match({"arxiv": "2101.00001"})["key"] == "Other"
    assert lib.match({"title": "Deep learning for NLP"})["key"] == "Smith2020Deep"
    assert lib.match({"title": "Something else"}) is None


def test_key_and_entry_formatting():
    assert make_key("Jean Dupont, Marie Curie", "2021", "Les réseaux de neurones") == "Dupont2021Reseaux"
    assert make_key("", "n.d.", "") == "AnonndUntitled"
    entry = format_entry("K", {"title": "Graphs & 100% fun", "authors": "Jean Dupont, Marie Curie", "year": "2021", "doi": "10.1/x"})
    assert entry.startswith("@article{K,") and r"\&" in entry and "Jean Dupont and Marie Curie" in entry

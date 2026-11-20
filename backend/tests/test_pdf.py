import pymupdf as fitz

from extract import extract_pdf, find_doi


def make_pdf(path):
    doc = fitz.open()
    for n in range(5):
        page = doc.new_page()
        page.insert_text((72, 40), "Journal of Testing — running header", fontsize=8)
        if n == 0:
            page.insert_text((72, 100), "Sparse Attention for Long Documents", fontsize=20)
            page.insert_text((72, 130), "Alice Martin and Bob Durand", fontsize=11)
            page.insert_text((72, 150), "Université de Test", fontsize=9)
            page.insert_text((72, 180), "Abstract", fontsize=11)
            page.insert_text((72, 200), "doi: 10.1234/test.5678 arXiv:2105.01234v2", fontsize=9)
        bodies = ["Sparse attention reduces the quadratic cost of transformers on long inputs.",
                  "Local windows capture nearby context efficiently.", "Global tokens connect distant parts of the text.",
                  "Experiments cover summarisation and question answering.", "References"]
        body = bodies[n]
        page.insert_text((72, 260), body, fontsize=10)
        if n == 4:
            page.insert_text((72, 280), "[1] Some cited paper, 2019.", fontsize=10)
    doc.save(str(path))


def test_pdf_metadata_and_cleaning(tmp_path):
    p = tmp_path / "paper.pdf"
    make_pdf(p)
    pages, meta = extract_pdf(p)
    assert meta["title"] == "Sparse Attention for Long Documents"
    assert meta["authors"] == "Alice Martin, Bob Durand"
    assert meta["doi"] == "10.1234/test.5678" and meta["arxiv"] == "2105.01234" and meta["year"] == "2021"
    text = " ".join(t for _, t in pages)
    assert "running header" not in text and "Some cited paper" not in text
    assert pages[0][0] == 1 and "quadratic cost" in text


def make_hal_pdf(path):
    doc = fitz.open()
    cover = doc.new_page()
    lines = ["HAL Id: hal-01234567", "https://hal.science/hal-01234567v1", "Submitted on 10 Mar 2022",
             "HAL is a multi-disciplinary open access archive", "Les ouvriers en France",
             "Jeanne Dupont", "To cite this version:",
             "Jeanne Dupont. Les ouvriers en France. Revue de test, 2019, 26, ⟨10.4000/test.3834⟩. ⟨hal-01234567⟩"]
    for n, line in enumerate(lines):
        cover.insert_text((40, 60 + 22 * n), line, fontsize=16 if n == 4 else 9)
    for body in ["La part des ouvriers a nettement baissé depuis 1982.", "Les emplois se déplacent vers la logistique."]:
        doc.new_page().insert_text((72, 100), body, fontsize=10)
    doc.set_metadata({"title": "Les ouvriers en France", "author": "Jeanne Dupont", "creationDate": "D:20261022061830"})
    doc.save(str(path))


def test_hal_cover_page(tmp_path):
    p = tmp_path / "hal.pdf"
    make_hal_pdf(p)
    pages, meta = extract_pdf(p)
    assert meta["year"] == "2019"
    assert find_doi("Revue, 2019, ⟨10.4000/test.3834⟩. ⟨hal-01⟩") == "10.4000/test.3834"
    text = " ".join(t for _, t in pages)
    assert "multi-disciplinary" not in text and pages[0][0] == 2 and "ouvriers" in text


def test_small_caps_and_long_author_lists(tmp_path):
    p = tmp_path / "caps.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Isolated Microlensing Events", fontsize=20)
    page.insert_text((72, 130), "Q IYUE  Q IAN , 1   AND  S HUDE  M AO 2", fontsize=9)
    page.insert_text((72, 200), "Abstract", fontsize=11)
    doc.save(str(p))
    assert extract_pdf(p)[1]["authors"] == "Qiyue Qian, Shude Mao"

    doc = fitz.open(str(p))
    doc.set_metadata({"author": "; ".join(f"Author{n} Name{n}" for n in range(40))})
    q = tmp_path / "many.pdf"
    doc.save(str(q))
    authors = extract_pdf(q)[1]["authors"]
    assert authors.startswith("Author0 Name0; Author1 Name1") and authors.endswith("; others") and authors.count(";") == 10

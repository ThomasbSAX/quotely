"""
Text and metadata extraction per format. Every extractor returns (pages, meta) where pages is a
list of (page_number | None, text) and meta holds title / authors / year / doi / arxiv.

PDF pipeline (PyMuPDF only, no layout model): O(P) pages, O(W) words.
  - text per page with de-hyphenation; OCR only for pages without a text layer
  - running headers/footers removed (lines repeated on ≥ 50 % of pages): O(lines)
  - reference list dropped after the last "References / Bibliographie" heading
  - title = largest font on page 1, authors = lines between title and abstract
  - repository cover pages (HAL, Cairn) are dropped from the text; the HAL "To cite this version"
    line gives the publication year, since the PDF creation date is the download date
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import config


_ocr_available: bool | None = None


def _is_ocr_available() -> bool:
    global _ocr_available
    if _ocr_available is None:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
            _ocr_available = shutil.which("tesseract") is not None
        except ImportError:
            _ocr_available = False
    return _ocr_available


_SOFFICE = shutil.which("soffice") or "/opt/homebrew/bin/soffice"
LIBREOFFICE_AVAILABLE = Path(_SOFFICE).exists()


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_tex(path: Path) -> tuple[str, dict]:
    """
    Parse LaTeX source file directly — preserves math perfectly.
    Strips LaTeX commands but keeps: section bodies, $...$ inline math,
    \\begin{equation}...\\end{equation}, \\begin{align}...
    """
    raw = path.read_text(encoding="utf-8", errors="ignore")

    # Extract metadata from preamble
    def _latex_arg(cmd: str) -> str:
        m = re.search(rf"\\{cmd}\{{([^}}]{{1,300}})\}}", raw)
        return m.group(1).strip() if m else ""

    title = _latex_arg("title") or path.stem
    authors = _latex_arg("author")
    year = _extract_year(_latex_arg("date") + raw[:3000])

    # Remove comments
    src = re.sub(r"%.*$", "", raw, flags=re.MULTILINE)

    # Keep only document body
    body_m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", src, re.DOTALL)
    body = body_m.group(1) if body_m else src

    # Replace section commands with readable headers
    body = re.sub(r"\\(?:sub)*section\*?\{([^}]+)\}", r"\n\n== \1 ==\n", body)

    # Preserve math blocks as-is (they're the key content)
    # Just clean surrounding whitespace, don't strip them

    # Strip remaining LaTeX commands but keep their arguments when meaningful
    # Remove environments that aren't content (figure env wrapper, but keep caption)
    body = re.sub(r"\\caption\{([^}]+)\}", r"[Caption: \1]", body)
    body = re.sub(r"\\label\{[^}]+\}", "", body)
    body = re.sub(r"\\ref\{[^}]+\}", "[ref]", body)
    body = re.sub(r"\\cite\{[^}]+\}", "[cite]", body)
    # Keep text formatting content
    body = re.sub(r"\\(?:textbf|textit|emph|underline|text)\{([^}]+)\}", r"\1", body)
    # Remove remaining single-argument commands (figures, includes, etc.)
    body = re.sub(r"\\[a-zA-Z]+\*?\{[^}]*\}", "", body)
    # Remove standalone commands
    body = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", body)
    # Clean up excess whitespace
    body = re.sub(r"\n{3,}", "\n\n", body)

    return body.strip(), {"title": title, "authors": authors, "year": year}


def _extract_docx(path: Path) -> tuple[str, dict]:
    from docx import Document as DocxDocument  # lazy — lxml C extension
    doc = DocxDocument(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    full_text = "\n".join(parts)
    title = parts[0][:120] if parts else path.stem
    year = _extract_year(full_text[:500])
    return full_text, {"title": title, "authors": "", "year": year}


def _extract_txt(path: Path) -> tuple[str, dict]:
    full_text = path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
    lines = full_text.splitlines()
    title = next((l.strip()[:120] for l in lines if l.strip()), path.stem)
    year = _extract_year(full_text[:500])
    return full_text, {"title": title, "authors": "", "year": year}


def _extract_pptx(path: Path) -> tuple[str, dict]:
    from pptx import Presentation
    prs = Presentation(str(path))
    title = path.stem
    # Try to get title from first slide's title placeholder
    if prs.slides:
        for shape in prs.slides[0].shapes:
            if shape.has_text_frame and shape.shape_type == 13:  # MSO_SHAPE_TYPE.PLACEHOLDER
                t = shape.text.strip()
                if t:
                    title = t[:120]
                    break
        if title == path.stem:
            # Fallback: first non-empty text in slide 1
            for shape in prs.slides[0].shapes:
                if shape.has_text_frame:
                    t = shape.text.strip()
                    if len(t) > 5:
                        title = t[:120]
                        break
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
    full_text = "\n".join(texts)
    year = _extract_year(full_text[:5000])
    return full_text, {"title": title, "authors": "", "year": year}


def _extract_xlsx(path: Path) -> tuple[str, dict]:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                rows.append(" | ".join(cells))
    wb.close()
    full_text = "\n".join(rows)
    year = _extract_year(full_text[:5000])
    return full_text, {"title": path.stem, "authors": "", "year": year}


def _extract_csv(path: Path) -> tuple[str, dict]:
    rows = []
    with open(path, encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if any(cell.strip() for cell in row):
                rows.append(" | ".join(row))
    full_text = "\n".join(rows)
    year = _extract_year(full_text[:5000])
    return full_text, {"title": path.stem, "authors": "", "year": year}


def _extract_image(path: Path) -> tuple[str, dict]:
    if not _is_ocr_available():
        raise ImportError(
            "pytesseract/Pillow not installed or Tesseract absent. "
            "Run: brew install tesseract && pip install pytesseract Pillow"
        )
    import pytesseract
    from PIL import Image as PILImage
    img = PILImage.open(str(path))
    full_text = pytesseract.image_to_string(img, lang="fra+eng")
    year = _extract_year(full_text[:5000])
    lines = full_text.splitlines()
    title = next((l.strip()[:120] for l in lines if len(l.strip()) > 5), path.stem)
    return full_text, {"title": title, "authors": "", "year": year}


def _extract_doc_via_libreoffice(path: Path) -> tuple[str, dict]:
    """Convert .doc / .ppt / .odt / .rtf to text via LibreOffice headless."""
    if not LIBREOFFICE_AVAILABLE:
        raise ImportError(
            "LibreOffice not found. Install with: brew install --cask libreoffice"
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            [_SOFFICE, "--headless", "--convert-to", "txt:Text",
             "--outdir", tmpdir, str(path)],
            capture_output=True,
            timeout=90,
            check=True,
        )
        txt_path = Path(tmpdir) / (path.stem + ".txt")
        if not txt_path.exists():
            raise RuntimeError(f"LibreOffice produced no output for {path.name}")
        return _extract_txt(txt_path)


def _extract_ipynb(path: Path) -> tuple[str, dict]:
    """Extract text from a Jupyter notebook (.ipynb)."""
    nb = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    parts: list[str] = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        if src.strip():
            parts.append(src)
        for output in cell.get("outputs", []):
            txt = "".join(output.get("text", []))
            if txt.strip():
                parts.append(txt)
    full_text = "\n\n".join(parts)
    title = next(
        (l.lstrip("# ").strip()[:120] for l in full_text.splitlines()
         if l.strip() and not l.startswith("```")),
        path.stem,
    )
    year = _extract_year(full_text[:5000])
    return full_text, {"title": title, "authors": "", "year": year}


def _fix_latex_encoding(text: str) -> str:
    """Fix common LaTeX font encoding artifacts in PyMuPDF text extraction."""
    # Combining accent + letter → precomposed Unicode character
    replacements = [
        ("´a", "á"), ("´e", "é"), ("´i", "í"), ("´o", "ó"), ("´u", "ú"),
        ("`a", "à"), ("`e", "è"), ("`i", "ì"), ("`o", "ò"), ("`u", "ù"),
        ("ˆa", "â"), ("ˆe", "ê"), ("ˆi", "î"), ("ˆo", "ô"), ("ˆu", "û"),
        ("¨a", "ä"), ("¨e", "ë"), ("¨i", "ï"), ("¨o", "ö"), ("¨u", "ü"),
        ("c¸", "ç"), ("c,", "ç"),
        # Common ligature issues
        ("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"), ("ﬃ", "ffi"), ("ﬄ", "ffl"),
    ]
    for bad, good in replacements:
        text = text.replace(bad, good)
    return text


_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20\d{2})(?!\d)")
_PDF_DATE_RE = re.compile(r"D:((?:19|20)\d{2})")


def _extract_year(text: str) -> str:
    import datetime

    m = _PDF_DATE_RE.search(text)
    if m:
        return m.group(1)
    limit = datetime.date.today().year + 1
    for m in _YEAR_RE.finditer(text):
        if int(m.group(1)) <= limit:
            return m.group(1)
    return "n.d."


def _infer_author_from_path(path: Path) -> str:
    """
    Guess author from folder path by extracting capitalized proper nouns
    from parent directory names.
    e.g. 'EcritspublicationsHarbulot' → 'Harbulot'
    """
    SKIP_LOWER = {
        'data', 'papers', 'documents', 'db', 'downloads', 'users',
        '.venv', 'backend', 'extension', 'node_modules', 'ragflow',
        'projets', 'perso', 'library', 'desktop',
    }
    GENERIC = {
        'Ecrits', 'Publications', 'Articles', 'Documents', 'Markdowns',
        'Projets', 'Downloads', 'Users', 'Library', 'Desktop', 'Papers',
        'Notes', 'Files', 'Folder',
    }
    for part in reversed(path.parent.parts):
        if part.lower() in SKIP_LOWER or part.startswith('.'):
            continue
        words = re.findall(r'[A-Z][a-z]{3,}', part)
        candidates = [w for w in words if w not in GENERIC]
        if candidates:
            return candidates[-1]
    return ""


def _infer_author_from_text(text: str) -> str:
    """Look for common author attribution patterns in the first 600 chars."""
    patterns = [
        r'[Pp]ar\s+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Ü][a-zà-ü]+)?)',
        r'[Aa]uteur\s*[:\-]\s*([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Ü][a-zà-ü]+)?)',
        r'[Aa]uthor\s*[:\-]\s*([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Ü][a-zà-ü]+)?)',
    ]
    snippet = text[:600]
    for pat in patterns:
        m = re.search(pat, snippet)
        if m:
            return m.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}⟨⟩]+[^\s\"<>{}⟨⟩.,;)\]])", re.I)
_ARXIV_RE = re.compile(r"arXiv:\s?(\d{4}\.\d{4,5})(v\d+)?", re.I)
_REFS_RE = re.compile(r"^\s*(?:\d+\.?\s*)?(references|bibliography|bibliographie|références|literature cited|works cited)\s*$", re.I | re.M)
_ABSTRACT_RE = re.compile(r"^\s*(abstract|résumé|summary|introduction)\b", re.I)
_HAL_CITE_RE = re.compile(r"To cite this version\s*:\s*(.+?)(?:⟨hal|⟨halshs|⟨tel|$)", re.S)
_COVER_RE = re.compile(r"^HAL Id\s*:|Distribution électronique Cairn\.info|Article disponible en ligne à l'adresse", re.M)
_SMALL_CAPS_RE = re.compile(r"\b([A-Z])\s(?=[A-Z]{2,}\b)")
_MAX_AUTHORS = 10
_BAD_META_TITLE = re.compile(r"^(untitled|microsoft word|document\d*|slide \d|.*\.(docx?|pdf|tex|dvi))", re.I)


def find_doi(text: str) -> str:
    m = _DOI_RE.search(text)
    return m.group(1) if m else ""


def find_arxiv(text: str) -> str:
    m = _ARXIV_RE.search(text)
    return m.group(1) if m else ""


def _strip_repeated_lines(pages: list[str]) -> list[str]:
    if len(pages) < 4:
        return pages

    def sig(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip())

    counts = Counter()
    for text in pages:
        lines = [l for l in text.splitlines() if l.strip()]
        counts.update({sig(l) for l in lines[:3] + lines[-3:]})
    repeated = {l for l, c in counts.items() if c >= max(3, len(pages) // 2) and len(l) < 120}
    out = []
    for text in pages:
        lines = text.splitlines()
        edge = {i for i, l in enumerate(lines) if l.strip()}
        edge = set(sorted(edge)[:3] + sorted(edge)[-3:])
        out.append("\n".join(l for i, l in enumerate(lines) if not (i in edge and sig(l) in repeated)))
    return out


def _drop_references(pages: list[str]) -> list[str]:
    total = sum(len(t) for t in pages)
    seen = 0
    cut = None
    for i, text in enumerate(pages):
        for m in _REFS_RE.finditer(text):
            if seen + m.start() > 0.4 * total:
                cut = (i, m.start())
        seen += len(text)
    if cut is None:
        return pages
    i, pos = cut
    return pages[:i] + [pages[i][:pos]]


def _title_and_authors(page) -> tuple[str, str]:
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"] for s in line["spans"]).strip()
            if text:
                lines.append((max(s["size"] for s in line["spans"]), line["bbox"][1], text))
    if not lines:
        return "", ""
    top = [l for l in lines if l[1] < page.rect.height * 0.6 and len(l[2]) > 3]
    if not top:
        return "", ""
    biggest = max(size for size, _, _ in top)
    title_lines = [l for l in top if abs(l[0] - biggest) < 0.6]
    title = " ".join(t for _, _, t in title_lines)
    after = [l for l in top if l[1] > title_lines[-1][1] and l[0] < biggest - 0.5]
    authors = []
    for _, _, text in after[:4]:
        if _ABSTRACT_RE.match(text) or "@" in text or len(text) > 160:
            break
        if re.search(r"(universit|institut|laborat|department|école|school|cnrs|inria|\d{4,})", text, re.I):
            continue
        authors.append(text)
        if len(authors) == 2:
            break
    names = _SMALL_CAPS_RE.sub(r"\1", " ".join(authors))
    names = re.sub(r"\b[A-Z][A-Z\-]+\b", lambda m: m.group(0).title() if len(m.group(0)) > 2 else m.group(0), names)
    names = re.sub(r"[\d*†‡§¶∗,]+(?=\s|$)", "", names)
    names = re.sub(r"\s+(and|et)\s+", ", ", names, flags=re.I)
    return title.strip()[:300], re.sub(r"\s+", " ", names).strip(" ,")[:300]


def _cap_authors(authors: str) -> str:
    names = [n for n in re.split(r"\s*;\s*|\s+and\s+", authors) if n]
    return "; ".join(names[:_MAX_AUTHORS] + ["others"]) if len(names) > _MAX_AUTHORS else authors


def _hal_citation(first_page: str, title: str) -> str:
    if not first_page.lstrip().startswith("HAL Id"):
        return ""
    m = _HAL_CITE_RE.search(first_page)
    return re.sub(r"\s+", " ", m.group(1)).replace(re.sub(r"\s+", " ", title), " ") if m else ""


def _ocr_page(page) -> str:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=200)
    return pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png"))), lang="fra+eng")


def extract_pdf(path: Path) -> tuple[list[tuple[int | None, str]], dict]:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    with fitz.open(str(path)) as doc:
        flags = fitz.TEXT_DEHYPHENATE | fitz.TEXT_PRESERVE_WHITESPACE
        raw = [page.get_text("text", flags=flags) for page in doc]
        if config.OCR_SCANNED_PAGES and _is_ocr_available():
            raw = [_ocr_page(doc[i]) if len(t.strip()) < 20 else t for i, t in enumerate(raw)]
        meta = doc.metadata or {}
        title, authors = _title_and_authors(doc[0]) if len(doc) else ("", "")

    raw = [_fix_latex_encoding(t) for t in raw]
    head = "\n".join(raw[:2])
    meta_title = (meta.get("title") or "").strip()
    if len(meta_title) > 8 and not _BAD_META_TITLE.match(meta_title):
        title = meta_title
    if (meta.get("author") or "").strip():
        authors = _cap_authors(meta["author"].strip())
    arxiv = find_arxiv(head)
    if arxiv:
        year = f"20{arxiv[:2]}"
    else:
        cite = _hal_citation(raw[0], title) if raw else ""
        year = _extract_year(cite) if cite else "n.d."
        if year == "n.d.":
            year = _extract_year(head + " " + (meta.get("creationDate") or ""))
    covers = {i for i, t in enumerate(raw[:2]) if _COVER_RE.search(t)}
    if len(covers) == len(raw):
        covers = set()
    pages = _drop_references(_strip_repeated_lines(["" if i in covers else t for i, t in enumerate(raw)]))
    return [(i + 1, t) for i, t in enumerate(pages) if t.strip()], {
        "title": title, "authors": authors, "year": year, "doi": find_doi(head), "arxiv": arxiv,
    }


def extract(path: Path) -> tuple[list[tuple[int | None, str]], dict]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    text, meta = _EXTRACTORS[suffix](path)
    head = text[:4000]
    meta.setdefault("doi", find_doi(head))
    meta.setdefault("arxiv", find_arxiv(head))
    return [(None, text)], meta


_EXTRACTORS = {
    ".tex": _extract_tex,
    ".docx": _extract_docx,
    ".doc": _extract_doc_via_libreoffice,
    ".odt": _extract_doc_via_libreoffice,
    ".rtf": _extract_doc_via_libreoffice,
    ".ppt": _extract_doc_via_libreoffice,
    ".md": _extract_txt,
    ".txt": _extract_txt,
    ".pptx": _extract_pptx,
    ".xlsx": _extract_xlsx,
    ".xls": _extract_xlsx,
    ".csv": _extract_csv,
    ".ipynb": _extract_ipynb,
    **{ext: _extract_image for ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp")},
}

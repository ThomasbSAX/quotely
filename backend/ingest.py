"""
Document ingestion: extraction → metadata → section-aware chunking → embedding → store.

W = words in a document, C = chunks (≈ W / (CHUNK_WORDS − CHUNK_OVERLAP)), N = rows in store.
  extraction      O(W) (+ OCR / PDF layout cost)
  chunking        O(W)
  embedding       O(C · L²) with L ≤ 512 tokens per chunk
  replace in store O(N·d) only when a previously indexed file changed, else O(C·d)
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import config
import embeddings as embed
from vector_store import VectorStore

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

def _extract_pdf(path: Path) -> tuple[str, dict]:
    import fitz  # lazy — C extension, avoid crash on module load
    import pymupdf4llm
    """
    Primary: pymupdf4llm → markdown with structure + OCR fallback on complex pages.
    Secondary: OCR of embedded images (figures, diagrams).
    """
    doc = fitz.open(str(path))
    meta = doc.metadata or {}

    # --- pymupdf4llm: markdown with multi-column layout, table support, OCR ---
    try:
        md_text = pymupdf4llm.to_markdown(str(path), show_progress=False)
    except Exception:
        # Fallback to raw text if pymupdf4llm fails
        md_text = "\n".join(page.get_text("text") for page in doc)

    # --- OCR of embedded images (figures, diagrams, formula images) ---
    img_texts: list[str] = []
    if _is_ocr_available():
        import pytesseract
        from PIL import Image as PILImage
        seen_xrefs: set[int] = set()
        for page in doc:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    base = doc.extract_image(xref)
                    w, h = base["width"], base["height"]
                    # Skip tiny images (icons, decorations < 50×50 px)
                    if w < 50 or h < 50:
                        continue
                    pil_img = PILImage.open(io.BytesIO(base["image"]))
                    ocr = pytesseract.image_to_string(pil_img, lang="fra+eng").strip()
                    if ocr:
                        img_texts.append(f"[Figure/Image]: {ocr}")
                except Exception:
                    pass

    doc.close()

    full_text = md_text
    if img_texts:
        full_text += "\n\n" + "\n\n".join(img_texts)

    # Fix LaTeX font encoding artifacts across the full text
    full_text = _fix_latex_encoding(full_text)

    title = meta.get("title", "").strip()
    author = meta.get("author", "").strip()
    year = _extract_year(meta.get("creationDate", "") + " " + full_text[:2000])

    if not title:
        # Look for first markdown heading or substantial line (skip date-only lines)
        date_re = re.compile(r"^\d{1,2}\s+\w+\s+\d{4}$|^\d{4}$")
        for line in full_text.splitlines():
            line = line.lstrip("#").strip()
            if len(line) > 15 and not date_re.match(line):
                title = line[:120]
                break

    # Fix common LaTeX font encoding artifacts in extracted text
    title = _fix_latex_encoding(title)
    author = _fix_latex_encoding(author)

    return full_text, {"title": title, "authors": author, "year": year}


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
# BibTeX helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]", "", text)
    return text


def _extract_lastname(author_str: str) -> str:
    """Extract a clean last name from author string (name or email)."""
    # Take first author only
    first = re.split(r"[;]", author_str)[0].strip()
    # Email: louise.budzynski@psl.eu → budzynski
    if "@" in first:
        local = first.split("@")[0]           # louise.budzynski
        parts = re.split(r"[.\-_]", local)
        last = parts[-1] if len(parts) > 1 else parts[0]
        return _slugify(last).capitalize()
    # "Last, First" format
    if "," in first:
        return _slugify(first.split(",")[0].strip()).capitalize()
    # "First Last" or "First M. Last"
    parts = first.split()
    return _slugify(parts[-1]).capitalize() if parts else "Unknown"


def _make_bibtex_key(authors: str, year: str, title: str) -> str:
    lastname = _extract_lastname(authors) if authors else "Unknown"
    # Short year: 2025 → 25, n.d. → nd
    if re.match(r"^\d{4}$", year):
        short_year = year[-2:]
    elif year == "n.d.":
        short_year = "nd"
    else:
        short_year = year
    # One distinctive title word (skip stop words + markdown artifacts)
    stop = {"a", "an", "the", "de", "du", "des", "le", "la", "les", "et",
            "en", "un", "une", "of", "in", "on", "to", "for", "cours", "n"}
    clean_title = re.sub(r"[*#\-–—]+", " ", title)  # strip markdown bold/headers
    words = [_slugify(w) for w in clean_title.split()
             if _slugify(w) and _slugify(w).lower() not in stop and len(_slugify(w)) > 2]
    title_word = words[0].capitalize() if words else "Untitled"
    return f"{lastname}{short_year}{title_word}"


def _make_bibtex_entry(key: str, title: str, authors: str, year: str, file_path: str) -> str:
    return (
        f"@article{{{key},\n"
        f"  title   = {{{title}}},\n"
        f"  author  = {{{authors or 'Unknown'}}},\n"
        f"  year    = {{{year}}},\n"
        f"  note    = {{File: {file_path}}}\n"
        f"}}"
    )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"(?=^#{1,3} |^== .+ ==$)", re.MULTILINE)
_CAPTION_RE = re.compile(r"\[Caption:[^\]]{10,}\]|\[Figure/Image\]:[^\n]{10,}")
_HEADER_RE = re.compile(r"(#{1,3} [^\n]+|== [^\n]+ ==)")


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    step = config.CHUNK_WORDS - config.CHUNK_OVERLAP
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start:start + config.CHUNK_WORDS]))
        if start + config.CHUNK_WORDS >= len(words):
            break
    return chunks


def _chunk_markdown(text: str) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    buffered = 0

    def flush() -> None:
        nonlocal buffer, buffered
        if buffer:
            chunks.append(" ".join(buffer))
        buffer, buffered = [], 0

    for part in _SECTION_RE.split(text):
        part = part.strip()
        if not part:
            continue
        chunks.extend(c.strip() for c in _CAPTION_RE.findall(part))
        n = len(part.split())
        if n <= config.CHUNK_WORDS:
            if buffered + n > config.CHUNK_WORDS:
                flush()
            buffer.append(part)
            buffered += n
            continue
        flush()
        header_m = _HEADER_RE.match(part)
        header = header_m.group(0) + " — " if header_m else ""
        chunks.extend(header + c for c in _chunk_text(part))
    flush()
    return [c for c in chunks if len(c.split()) >= config.MIN_CHUNK_WORDS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_EXTRACTORS = {
    ".pdf": (_extract_pdf, _chunk_markdown),
    ".tex": (_extract_tex, _chunk_markdown),
    ".docx": (_extract_docx, _chunk_text),
    ".doc": (_extract_doc_via_libreoffice, _chunk_text),
    ".odt": (_extract_doc_via_libreoffice, _chunk_text),
    ".rtf": (_extract_doc_via_libreoffice, _chunk_text),
    ".ppt": (_extract_doc_via_libreoffice, _chunk_text),
    ".md": (_extract_txt, _chunk_markdown),
    ".txt": (_extract_txt, _chunk_markdown),
    ".pptx": (_extract_pptx, _chunk_text),
    ".xlsx": (_extract_xlsx, _chunk_text),
    ".xls": (_extract_xlsx, _chunk_text),
    ".csv": (_extract_csv, _chunk_text),
    ".ipynb": (_extract_ipynb, _chunk_markdown),
    **{ext: (_extract_image, _chunk_text) for ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp")},
}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in config.SUPPORTED_EXTENSIONS and not _in_db(path)


def _in_db(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(config.DB_PATH.resolve())
        return True
    except ValueError:
        return False


def _unique_key(store: VectorStore, base: str, path: str) -> str:
    key, n = base, 0
    while store.key_owner(key) not in (None, path):
        n += 1
        key = base + chr(ord("a") + (n - 1) % 26) * (1 + (n - 1) // 26)
    return key


def extract(path: Path) -> tuple[str, dict, list[str]]:
    extractor, chunker = _EXTRACTORS[path.suffix.lower()]
    text, meta = extractor(path)
    return text, meta, chunker(text)


def ingest_file(path: Path, store: VectorStore, sha: str | None = None) -> dict | None:
    """Index `path` into `store`; returns None when the exact file content is already indexed."""
    path = Path(path).resolve()
    sha = sha or file_hash(path)
    spath = str(path)
    if store.path_hash(spath) == sha:
        return None

    text, meta, chunks = extract(path)
    if not chunks:
        raise ValueError("No text extracted from document.")

    title = re.sub(r"^[#*\s]+|[*\s]+$", "", meta["title"] or "") or path.stem
    authors = meta["authors"] or _infer_author_from_path(path) or _infer_author_from_text(text)
    year = meta["year"]

    title_chunk = title + (f" — {authors}" if authors else "")
    documents = [title_chunk] + chunks
    vectors = embed.embed_passages([title_chunk] + [f"{title}\n{c}" for c in chunks])

    with store.lock:
        previous = store.key_for_path(spath)
        store.delete_where("file_path", spath)
        key = previous or _unique_key(store, _make_bibtex_key(authors, year, title), spath)
        base_meta = {
            "bibtex_key": key,
            "title": title,
            "authors": authors,
            "year": year,
            "file_path": spath,
            "file_hash": sha,
            "bibtex_entry": _make_bibtex_entry(key, title, authors, year, spath),
        }
        store.upsert(
            ids=[f"{sha[:16]}:{i}" for i in range(len(documents))],
            embeddings=vectors,
            documents=documents,
            metadatas=[{**base_meta, "chunk_index": i - 1} for i in range(len(documents))],
        )

    print(f"[Quotely] Indexed '{title}' ({len(documents)} chunks) → {key}")
    return {"bibtex_key": key, "title": title, "chunks_indexed": len(documents)}


def remove_file(path: Path, store: VectorStore) -> int:
    return store.delete_where("file_path", str(Path(path).resolve()))

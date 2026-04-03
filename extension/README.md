# Quotely — AI Citation Autocomplete for VS Code

**Quotely** is a VS Code extension that suggests relevant citations as you write academic papers in LaTeX or Markdown — powered entirely by your local document collection, with no data sent to the cloud.

![Quotely banner](https://raw.githubusercontent.com/ThomasbSAX/quotely/main/logo.png)

---

## What is Quotely?

When writing a scientific article, finding the right source at the right moment is the main friction point. Quotely solves this:

- You write a sentence like *"The algorithm converges under convexity assumptions..."*
- Press **`Cmd+Shift+C`** (or type `\cite{`)
- Quotely searches your corpus and suggests the most relevant paper
- Accept → the `\cite{Key}` and full BibTeX entry are inserted automatically

All processing is **local**: your documents never leave your machine.

---

## Features

- **Inline citation suggestions** — triggered by `\cite{` or `[@` (Pandoc), or manually with `Cmd+Shift+C`
- **Semantic search** — finds conceptually related papers even without exact keyword match
- **Auto-bibliography** — accepted citations are appended to a BibTeX block at the end of your file
- **Auto-indexing** — drop any file into `data/` and it is parsed and indexed in the background
- **File decorations** — green **✓** badge on indexed files in the VS Code Explorer
- **Click to open** — every suggestion shows a link to open the source document
- **Folder indexing** — `Cmd+Shift+P → "Quotely: Indexer un dossier"` to add any folder to the index
- **Supports 14 formats** — PDF, LaTeX, Word, Markdown, PowerPoint, Excel, CSV, images (OCR), notebooks

---

## Quick Start

### Option A — VS Code Marketplace (recommended)

1. Search **"Quotely"** in the VS Code Extensions panel and click **Install**
2. On first activation, Quotely will offer to install the backend automatically (~300 MB, one-time)
3. Drop your PDFs/articles into `~/.quotely/data/papers/` and start writing

### Option B — Manual setup

```bash
git clone https://github.com/ThomasbSAX/quotely.git
cd quotely

# Install backend + models (macOS/Linux)
bash setup.sh
```

Then install the extension from `extension/quotely-1.2.0.vsix`.

> **Requirements:**  Python 3.8+, macOS or Linux (Windows support coming soon).

---

## Usage Guide

### 1. Add your documents

Drop PDF, DOCX, TEX, Markdown, or any other supported file into the `data/` folder.
Sub-folders are supported — organize however you like:

```
data/
├── papers/
│   ├── smith2023convexity.pdf
│   └── jones2022learning.tex
└── mybiblio/
    ├── chapter1/
    └── chapter2/
```

Files are **indexed automatically** within seconds of being added. No restart needed.

### 2. Get citation suggestions

Place your cursor after `\cite{` in a LaTeX file (or `[@` in Markdown) and press **`Cmd+Shift+C`**:

```latex
The gradient descent algorithm converges under smoothness conditions \cite{
%                                                                        ↑ Cmd+Shift+C here
```

Quotely analyzes the last 3 paragraphs and shows a ranked list of relevant papers.
Select one → the key is inserted and the BibTeX entry is appended to your bibliography section.

You can also trigger suggestions at the **end of any line** (≥ 40 characters) — Quotely will insert a full `\cite{Key}`.

### 3. Search across your corpus

**`Cmd+Shift+F`** opens a search bar with two modes:

| Mode | Description |
|------|-------------|
| Keyword | Exact substring search across all indexed chunks |
| Semantic | Embedding-based similarity — finds related concepts |

### 4. Commands

| Command | Shortcut | Description |
|---------|----------|-------------|
| Quotely: Suggest Citation | `Cmd+Shift+C` | Citation picker for current context |
| Quotely: Search Documents | `Cmd+Shift+F` | Keyword / semantic corpus search |
| Quotely: List Indexed Papers | — | Browse all papers, copy BibTeX |
| Quotely: Insert Bibliography | — | Insert full BibTeX block at end of document |
| Quotely: Indexer un dossier | — | Add a folder to the index |
| Quotely: Réindexer tout le corpus | — | Clear DB and re-index everything |
| Quotely: Open Papers Folder | — | Open `data/papers/` in Finder/Explorer |

---

## Performance

Benchmarks on Apple M-series CPU, corpus of 300 articles (7975 chunks), models warm.

### Latency per query

| Stage | Time |
|-------|------|
| Query embedding (bi-encoder) | ~7 ms |
| Vector search (ChromaDB HNSW) | ~5 ms |
| Reranking (cross-encoder, 30 candidates) | ~200 ms |
| **Total end-to-end** | **~200–400 ms** |

> Cold start (first query after VS Code launch): ~15–20 s while models load into RAM.

### Indexing speed

| Format | Speed |
|--------|-------|
| Encode 1 chunk (~300 words) | ~1.5 ms |
| PDF parsing (pymupdf4llm) | 0.5–3 s |
| DOCX parsing | < 0.1 s |

### Corpus scaling

| Corpus size | Est. chunks | RAM (index) | Initial indexing |
|-------------|-------------|-------------|------------------|
| 50 docs | ~2 000 | ~120 MB | ~2 min |
| 300 docs | ~8 000 | ~200 MB | ~10 min |
| 1 000 docs | ~30 000 | ~400 MB | ~35 min |

---

## Resource Usage

### RAM (backend running)

| Component | RAM |
|-----------|-----|
| Bi-encoder (all-MiniLM-L6-v2) | ~380 MB |
| Cross-encoder reranker (mMiniLMv2-L12) | ~444 MB |
| ChromaDB index (300 articles) | ~150 MB |
| **Total backend** | **~950 MB** |

> Runs in the background — VS Code itself uses ~500 MB additionally.
> On machines with 8 GB RAM or more, this is comfortable.

### Disk

| Item | Size |
|------|------|
| Python virtual environment (`.venv/`) | ~400 MB |
| AI models cache (`~/.cache/huggingface/`) | ~560 MB |
| ChromaDB index (300 articles) | ~80 MB |
| Extension `.vsix` | < 1 MB |

**Total first install: ~1 GB disk space.**

### CPU

- **Indexing**: uses ~100% of one core for a few seconds per document (embedding batch)
- **Suggestions**: ~200 ms peak, then idle
- **Idle**: < 0.5% CPU (file watcher + FastAPI waiting)

---

## Architecture

```
User query (last 3 paragraphs of their document)
        │
        ▼
  Query cleaning               ← strip LaTeX commands, \cite{}, \begin{}…
        │
        ▼
  Bi-encoder                   ← all-MiniLM-L6-v2 (384-dim embedding)
        │
        ▼
  ChromaDB HNSW search         ← cosine similarity, n×10 candidate chunks
        │
        ▼
  Deduplication + context      ← top-2 chunks per paper concatenated
        │
        ▼
  Cross-encoder reranker       ← mmarco-mMiniLMv2-L12-H384-v1 (multilingual)
        │
        ▼
  Top-n results                ← sigmoid-normalized scores [0, 1]
```

### Retrieval improvements

- **Title-prefix embeddings**: every chunk is embedded as `"Titre: {title}\n{text}"` so the title is always part of the vector representation — retrieval works even if the query only mentions the title
- **Dedicated title chunk**: a title-only entry is stored per paper for direct title matching
- **Author inference**: extracted from folder structure (e.g. `DocumentsHarbulot/` → author = Harbulot) and from document text patterns ("Par Prénom Nom")

---

## Models Used

### Bi-encoder — `sentence-transformers/all-MiniLM-L6-v2`

| Parameter | Value |
|-----------|-------|
| Architecture | MiniLM-L6 (6-layer transformer) |
| Embedding dimension | 384 |
| Size on disk | **87 MB** |
| Languages | English (works well in French) |
| Role | Encodes query + chunks → cosine similarity |

### Cross-encoder — `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`

| Parameter | Value |
|-----------|-------|
| Architecture | MiniLM-L12 (12 layers) |
| Size on disk | **470 MB** |
| Languages | **Multilingual** (French, English, Arabic, Chinese, …) |
| Trained on | MS MARCO multilingual |
| Role | Precise relevance scoring for each (query, document) pair |

### Vector database — ChromaDB 0.5.3

- Embedded, no server required
- HNSW index with cosine metric
- Persisted locally in `data/db/`

---

## Supported Formats

| Format | Parser | Notes |
|--------|--------|-------|
| `.pdf` | pymupdf4llm | Structured markdown output, multi-column, embedded image OCR |
| `.tex` | Native LaTeX parser | Preserves `$...$` math perfectly |
| `.docx` | python-docx | Paragraphs + tables |
| `.doc` | LibreOffice headless | Legacy Word format |
| `.md`, `.txt` | Direct read | Section-aware chunking on `##` headers |
| `.pptx` | python-pptx | All text frames, slide by slide |
| `.ppt` | LibreOffice headless | Legacy PowerPoint |
| `.odt`, `.rtf` | LibreOffice headless | Open/Rich Text formats |
| `.xlsx`, `.xls` | openpyxl | All sheets, pipe-separated cells |
| `.csv` | stdlib csv | — |
| `.ipynb` | JSON | Cell source + text outputs |
| `.png`, `.jpg`, `.tiff`, etc. | pytesseract OCR | Requires `brew install tesseract` |

---

## Settings

```jsonc
{
  "quotely.backendUrl": "http://127.0.0.1:7331",  // backend port
  "quotely.contextParagraphs": 3,                  // paragraphs used as query context
  "quotely.maxSuggestions": 5,                     // number of candidates shown
  "quotely.triggerOnCite": true,                   // auto-trigger on \cite{
  "quotely.projectPath": "/path/to/quotely"        // set by setup.sh or auto-setup
}
```

---

## License

MIT — free to use, modify, and distribute.

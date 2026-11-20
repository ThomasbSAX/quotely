# Quotely — Smart Citation Autocomplete

**Write faster. Cite better.**

Quotely watches what you write and suggests the most relevant papers from your library — directly in your editor, without leaving your document.

It works fully offline. No API key, no internet, no data sent anywhere.

![Quotely banner](logo.png)

---

## Install

Search for **Quotely** in the VS Code Marketplace, or install from the Extensions panel (`Cmd+Shift+X`).

On first launch, Quotely installs its Python backend automatically (~5 minutes, one time only). You only need **Python 3.9+** on your machine.

---

## Usage

| Action | Shortcut |
|---|---|
| Suggest a citation at cursor | `Cmd+Shift+C` / `Ctrl+Shift+C` |
| Search your library | `Cmd+Shift+F` / `Ctrl+Shift+F` |

Or open the Command Palette (`Cmd+Shift+P`) and type **Quotely**:

- **Quotely: Index a folder** — add any folder of documents to your library
- **Quotely: Re-index everything** — rebuild the index from scratch
- **Quotely: List Indexed Papers** — browse your library
- **Quotely: Setup / Repair Backend** — if the backend isn't starting

---

## How it works

1. **Indexing** — Quotely reads your documents, splits them into sections, and creates multilingual semantic embeddings locally (ONNX, no GPU needed)
2. **Retrieval** — When you write, Quotely embeds the current paragraph and finds the most semantically similar content in your library
3. **Reranking** — A cross-encoder re-scores the top candidates for maximum relevance
4. **Suggestion** — The best matches appear as `\cite{Key}` (LaTeX) or `[@Key]` (Markdown/Pandoc), with BibTeX auto-inserted at the bottom of your file

---

## Supported formats

PDF · LaTeX (.tex) · Word (.docx) · Markdown · PowerPoint (.pptx) · Excel · CSV · Jupyter Notebooks · Images (OCR)

---

## Multi-project support

Each VS Code window stays scoped to its own workspace. Open three different research projects in three windows — each one only suggests papers from its own folder, plus your shared library in `data/papers/`.

---

## Requirements

- Python 3.9 or higher
- VS Code 1.85+
- No GPU, no internet needed after setup

---

## Privacy

Everything runs on your machine. Your documents, your embeddings, your data — none of it ever leaves your computer.

---

## Development

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q tests
python main.py            # http://127.0.0.1:7331, every request needs the X-Quotely-Client header
```

Environment variables: `QUOTELY_HOME` (data root), `QUOTELY_PORT`, `QUOTELY_EMBED_MODEL`, `QUOTELY_EMBED_MODEL_PATH` (local ONNX export, e.g. a fine-tuned model), `QUOTELY_RERANKER`.

The index lives in `data/db/index/` and is saved atomically. Bulk indexing checkpoints every 10 files; a full re-index is built in `data/db/staging/` and swapped in at the end, so an interrupted run resumes where it stopped on the next start.

---

## Source

[github.com/ThomasbSAX/quotely](https://github.com/ThomasbSAX/quotely)

---

MIT License

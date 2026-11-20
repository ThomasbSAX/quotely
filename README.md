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

### Writing assistant

While you write (LaTeX, Markdown or plain text), Quotely reads the paragraph you are working on and marks, with a discreet dotted underline, every sentence that a document of your library supports:

- **📚** a source you have not cited yet supports the sentence
- **📝** a source you already cite has a passage that matches; handy as a reminder or a quotation

Hover the sentence (or press `Cmd+.` / `Ctrl+.`) to see the matching passage, with its page, and choose:

- **Cite**: inserts `\cite{Key}` (or `[@Key]`) at the end of the sentence, merged with an existing citation if there is one, and adds the BibTeX entry to your `.bib` file
- **Note for me**: inserts a comment under the line with the passage and the page (`% Quotely ▸ [Key, p. 4] « … »`)
- **Open**: opens the source at that page
- **Ignore**: never suggests this source for this sentence again

If the document has no bibliography yet, Quotely asks before creating `references.bib` and declaring it (`\bibliography{…}` / `\addbibresource{…}` for LaTeX, the YAML header for Pandoc Markdown).

| Action | Shortcut |
|---|---|
| Suggested sources in this document | `Cmd+Alt+R` / `Ctrl+Alt+R` |
| Suggest a citation at cursor | `Cmd+Shift+C` / `Ctrl+Shift+C` |
| Search your library | `Cmd+Shift+F` / `Ctrl+Shift+F` |

Command Palette (**Quotely**): *Analyze the whole document*, *Complete the bibliography* (adds every cited key that is missing), *Enable / disable the writing assistant*, *Index a folder*, *Re-index everything*, *List indexed papers*, *Setup / Repair backend*.

Settings: `quotely.assistant.sensitivity` (`faible` / `normale` / `élevée`), `quotely.assistant.delay`, `quotely.suggestAtLineEnd`, `quotely.onlineMetadata` (off by default).

Sentences about the document itself ("the next section", "Table 3") and hollow academic phrases ("further research is needed") are not flagged. When a sentence seems to say the opposite of the matched passage (negation, opposite direction, numbers that do not match), the suggestion is kept but its confidence is halved and the hover shows **À vérifier : la source semble dire l'inverse**.

---

## How it works

1. **Indexing**: PDFs are read page by page (running headers and the reference list are removed; scanned pages are OCR'd), title, authors, year, DOI and arXiv id are extracted, and the text is split into passages of whole sentences (≈120 words) that keep their page and section. If a `.bib` file sits next to your documents, its keys and entries are reused, so suggestions use *your* keys.
2. **Retrieval**: multilingual e5 embeddings (int8 ONNX, ~120 MB, CPU) fused with BM25 on exact terms (reciprocal rank fusion), grouped per document.
3. **Precision mode** (optional, `QUOTELY_PRECISION=1`): a multilingual cross-encoder (jina-reranker-v2, int8 export) reorders the top results of library searches (not the live assistant). On the benchmark it lifts Recall@1 from 0.82–0.88 to 1.00, for a 280 MB download, about 0.9 GB more RAM and roughly 0.8 s per search on CPU.

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

Everything runs on your machine. Your documents, your embeddings, your data — none of it ever leaves your computer. The only exception is opt-in: with `quotely.onlineMetadata`, the DOI or arXiv id of a newly indexed paper is sent to Crossref or arXiv to complete its metadata (answers are cached in `data/db/online_metadata.json`).

---

## Development

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q tests
python main.py            # http://127.0.0.1:7331, every request needs the X-Quotely-Client header
```

Environment variables: `QUOTELY_HOME` (data root), `QUOTELY_PORT`, `QUOTELY_EMBED_MODEL` / `QUOTELY_EMBED_FILE`, `QUOTELY_EMBED_MODEL_PATH` (local ONNX export, e.g. a fine-tuned model), `QUOTELY_PRECISION`, `QUOTELY_RERANKER`, `QUOTELY_OCR`, `QUOTELY_ASSIST_THRESHOLD`, `QUOTELY_CONTRADICTION` (0 disables the contradiction check), `QUOTELY_ASSIST_GENERIC_MARGIN`, `QUOTELY_RERANKER_FILE` (ONNX file of the reranker, default `onnx/model_quantized.onnx`), `QUOTELY_ONLINE_METADATA`, `FASTEMBED_CACHE_PATH` (models, default `data/db/models`).

Evaluation: with a backend running on the library, `python benchmarks/evaluate.py --label base` scores `/assist` on `benchmarks/sentences.json` (tuning set; `--set validation` for the held-out set, never used for tuning) at thresholds 0.75 → 0.90 (precision, recall, F1, Recall@1/@3, false positives, contradictions) and resumes if interrupted.

The index lives in `data/db/index/` and is saved atomically. Bulk indexing checkpoints every 10 files; a full re-index is built in `data/db/staging/` and swapped in at the end, so an interrupted run resumes where it stopped on the next start.

---

## Source

[github.com/ThomasbSAX/quotely](https://github.com/ThomasbSAX/quotely)

---

MIT License

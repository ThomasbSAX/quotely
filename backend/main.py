from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile

import config
import embeddings as embed
import indexer
from ingest import ingest_file, is_supported
from models import (
    FolderIngestRequest, FolderIngestResult, IngestResponse, PaperInfo, ReindexResult,
    SearchResponse, SearchResult, SuggestRequest, SuggestResponse,
)
from search import search
from watcher import start_watcher


def _boot() -> None:
    embed.embedder()
    indexer.startup()
    embed.reranker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.PAPERS_PATH.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_boot, daemon=True).start()
    observer = start_watcher()
    yield
    observer.stop()
    indexer.store().save()


def require_client(x_quotely_client: str = Header(default="")) -> None:
    if not x_quotely_client:
        raise HTTPException(status_code=403, detail=f"Missing {config.CLIENT_HEADER} header.")


app = FastAPI(title="Quotely", version=config.VERSION, lifespan=lifespan, dependencies=[Depends(require_client)])


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": config.VERSION,
        "indexed_chunks": indexer.store().count(),
        "indexing": indexer.progress,
        "pid": os.getpid(),
        **embed.status(),
    }


@app.get("/progress")
def progress():
    return indexer.progress


@app.post("/suggest", response_model=SuggestResponse)
def suggest(req: SuggestRequest):
    return SuggestResponse(citations=search(req.text, n=req.n, workspace_path=req.workspace_path))


@app.post("/ingest", response_model=IngestResponse)
async def ingest_upload(file: UploadFile = File(...)):
    dest = config.PAPERS_PATH / Path(file.filename or "").name
    if not is_supported(dest):
        raise HTTPException(status_code=400, detail=f"Unsupported format: {dest.suffix}")
    dest.write_bytes(await file.read())
    result = ingest_file(dest, indexer.store())
    if result is None:
        raise HTTPException(status_code=409, detail="File already indexed.")
    indexer.store().save()
    return IngestResponse(status="ok", **result)


@app.get("/papers", response_model=list[PaperInfo])
def list_papers():
    col = indexer.store()
    seen: dict[str, PaperInfo] = {}
    for meta in col.metadatas[: col.count()]:
        if meta["bibtex_key"] not in seen:
            seen[meta["bibtex_key"]] = PaperInfo(**{k: meta[k] for k in PaperInfo.model_fields})
    return list(seen.values())


@app.get("/chunks")
def get_chunks_for_paper(key: str, n: int = 4):
    col = indexer.store()
    docs = sorted((col.documents[i] for i in col.rows_where("bibtex_key", key)), key=len, reverse=True)
    return {"bibtex_key": key, "chunks": docs[:n]}


@app.get("/search", response_model=SearchResponse)
def doc_search(q: str, mode: str = "keyword", n: int = 20):
    col = indexer.store()
    if mode == "semantic":
        results = [
            SearchResult(bibtex_key=c.bibtex_key, title=c.title, authors=c.authors, year=c.year,
                         excerpt="", chunk_index=0, file_path=c.file_path)
            for c in search(q, n=n)
        ]
        return SearchResponse(query=q, mode=mode, results=results)

    needle = q.lower()
    results: list[SearchResult] = []
    for doc, meta in zip(col.documents[: col.count()], col.metadatas):
        if needle in doc.lower():
            results.append(SearchResult(
                bibtex_key=meta["bibtex_key"], title=meta["title"], authors=meta["authors"],
                year=meta["year"], excerpt=_make_excerpt(doc, q),
                chunk_index=meta.get("chunk_index", 0), file_path=meta["file_path"],
            ))
            if len(results) >= n:
                break
    return SearchResponse(query=q, mode=mode, results=results)


def _make_excerpt(chunk: str, query: str, context: int = 120) -> str:
    idx = chunk.lower().find(query.lower())
    if idx == -1:
        return chunk[: context * 2].strip()
    start = max(0, idx - context // 2)
    end = min(len(chunk), idx + len(query) + context // 2)
    return ("…" if start > 0 else "") + chunk[start:end].strip() + ("…" if end < len(chunk) else "")


@app.post("/ingest-folder", response_model=FolderIngestResult)
def ingest_folder(req: FolderIngestRequest):
    folder = Path(req.path).expanduser().resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {req.path}")
    result = indexer.index_folder(folder)
    return FolderIngestResult(**result)


@app.post("/reindex", response_model=ReindexResult)
def reindex_all():
    return ReindexResult(**indexer.reindex())


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, reload=False)

"""
RagCite FastAPI backend — port 7331
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from ingest import get_collection, ingest_file, is_already_indexed, PAPERS_PATH, DATA_PATH
from watcher import SUPPORTED_EXTENSIONS, _is_db_file
from models import IngestResponse, PaperInfo, SearchResponse, SearchResult, SuggestRequest, SuggestResponse, FolderIngestRequest, FolderIngestResult, ReindexResult
from search import search
from watcher import index_existing_papers, start_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: index existing papers + start watcher
    print("[RagCite] Indexing existing papers...")
    index_existing_papers()
    start_watcher()
    yield


app = FastAPI(title="RagCite", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    col = get_collection()
    return {"status": "ok", "indexed_chunks": col.count()}


@app.post("/suggest", response_model=SuggestResponse)
def suggest(req: SuggestRequest):
    col = get_collection()
    if col.count() == 0:
        return SuggestResponse(citations=[])
    try:
        citations = search(req.text, n=req.n, workspace_path=req.workspace_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return SuggestResponse(citations=citations)


@app.post("/ingest", response_model=IngestResponse)
async def ingest_upload(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {suffix}")

    dest = PAPERS_PATH / file.filename
    dest.write_bytes(await file.read())

    if is_already_indexed(dest):
        raise HTTPException(status_code=409, detail="File already indexed.")

    try:
        result = ingest_file(dest)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return IngestResponse(
        status="ok",
        bibtex_key=result["bibtex_key"],
        title=result["title"],
        chunks_indexed=result["chunks_indexed"],
    )


@app.get("/papers", response_model=list[PaperInfo])
def list_papers():
    col = get_collection()
    if col.count() == 0:
        return []

    all_items = col.get(include=["metadatas"])
    seen: dict[str, PaperInfo] = {}
    for meta in all_items["metadatas"]:
        key = meta["bibtex_key"]
        if key not in seen:
            seen[key] = PaperInfo(
                bibtex_key=key,
                title=meta["title"],
                authors=meta["authors"],
                year=meta["year"],
                file_path=meta["file_path"],
                bibtex_entry=meta["bibtex_entry"],
            )
    return list(seen.values())


@app.get("/chunks")
def get_chunks_for_paper(key: str, n: int = 4):
    """Return the n longest chunks stored for a given bibtex_key."""
    col = get_collection()
    raw = col.get(where={"bibtex_key": key}, include=["documents"])
    docs = raw.get("documents") or []
    # Sort by length (longest = most content) and return top n
    docs_sorted = sorted(docs, key=len, reverse=True)
    return {"bibtex_key": key, "chunks": docs_sorted[:n]}


@app.get("/search", response_model=SearchResponse)
def doc_search(q: str, mode: str = "keyword", n: int = 20):
    """
    Search documents by keyword (fulltext) or semantic meaning.
    mode=keyword : substring match across all indexed chunks
    mode=semantic: embedding-based similarity search
    """
    col = get_collection()
    if col.count() == 0:
        return SearchResponse(query=q, mode=mode, results=[])

    if mode == "semantic":
        from search import search as semantic_search
        citations = semantic_search(q, n=n)
        results = [
            SearchResult(
                bibtex_key=c.bibtex_key,
                title=c.title,
                authors=c.authors,
                year=c.year,
                excerpt="",
                chunk_index=0,
                file_path="",
            )
            for c in citations
        ]
        return SearchResponse(query=q, mode=mode, results=results)

    # Keyword mode: ChromaDB fulltext contains filter
    raw = col.get(
        where_document={"$contains": q},
        include=["metadatas", "documents"],
        limit=n * 3,
    )
    seen_keys: set[str] = set()
    results: list[SearchResult] = []
    for meta, doc in zip(raw["metadatas"], raw["documents"]):
        key = meta["bibtex_key"]
        # Keep first (best) match per paper, but include multiple if distinct chunks
        excerpt = _make_excerpt(doc, q)
        results.append(
            SearchResult(
                bibtex_key=key,
                title=meta["title"],
                authors=meta["authors"],
                year=meta["year"],
                excerpt=excerpt,
                chunk_index=meta.get("chunk_index", 0),
                file_path=meta["file_path"],
            )
        )
        seen_keys.add(key)
        if len(results) >= n:
            break

    return SearchResponse(query=q, mode=mode, results=results)


def _make_excerpt(chunk: str, query: str, context: int = 120) -> str:
    """Return a short excerpt around the first occurrence of query in chunk."""
    idx = chunk.lower().find(query.lower())
    if idx == -1:
        return chunk[:context * 2].strip()
    start = max(0, idx - context // 2)
    end = min(len(chunk), idx + len(query) + context // 2)
    excerpt = chunk[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(chunk):
        excerpt = excerpt + "…"
    return excerpt


@app.post("/ingest-folder", response_model=FolderIngestResult)
def ingest_folder(req: FolderIngestRequest):
    """Index all supported files found recursively in the given folder path."""
    folder = Path(req.path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {req.path}")

    indexed = 0
    skipped = 0
    errors: list[str] = []

    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        if _is_db_file(p):
            continue
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if is_already_indexed(p):
            skipped += 1
            continue
        try:
            ingest_file(p)
            indexed += 1
        except Exception as e:
            errors.append(f"{p.name}: {e}")

    return FolderIngestResult(indexed=indexed, skipped=skipped, errors=errors)


@app.post("/reindex", response_model=ReindexResult)
def reindex_all():
    """Clear the vector DB and re-index everything from DATA_PATH (fresh embeddings)."""
    col = get_collection()
    all_ids = col.get(include=[])["ids"]
    if all_ids:
        col.delete(ids=all_ids)

    total = 0
    errors: list[str] = []

    for p in DATA_PATH.rglob("*"):
        if not p.is_file():
            continue
        if _is_db_file(p):
            continue
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            ingest_file(p)
            total += 1
        except Exception as e:
            errors.append(f"{p.name}: {e}")

    return ReindexResult(total=total, errors=errors)


if __name__ == "__main__":
    # Allow running from any directory
    sys.path.insert(0, str(Path(__file__).parent))
    uvicorn.run("main:app", host="127.0.0.1", port=7331, reload=False)

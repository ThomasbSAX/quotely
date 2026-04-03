from pydantic import BaseModel


class SuggestRequest(BaseModel):
    text: str
    n: int = 5
    workspace_path: str = ""  # if set, filter results to this folder


class CitationResult(BaseModel):
    bibtex_key: str
    title: str
    authors: str
    year: str
    score: float
    bibtex_entry: str
    file_path: str = ""


class SuggestResponse(BaseModel):
    citations: list[CitationResult]


class PaperInfo(BaseModel):
    bibtex_key: str
    title: str
    authors: str
    year: str
    file_path: str
    bibtex_entry: str


class SearchResult(BaseModel):
    bibtex_key: str
    title: str
    authors: str
    year: str
    excerpt: str       # matching chunk text
    chunk_index: int
    file_path: str


class IngestResponse(BaseModel):
    status: str
    bibtex_key: str
    title: str
    chunks_indexed: int


class SearchResponse(BaseModel):
    query: str
    mode: str
    results: list[SearchResult]


class FolderIngestRequest(BaseModel):
    path: str


class FolderIngestResult(BaseModel):
    indexed: int
    skipped: int
    errors: list[str]


class ReindexResult(BaseModel):
    total: int
    errors: list[str]

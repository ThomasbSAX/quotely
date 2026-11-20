from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Passage(BaseModel):
    text: str
    page: Optional[int] = None
    section: str = ""
    title_row: bool = False


class CitationResult(BaseModel):
    bibtex_key: str
    title: str
    authors: str
    year: str
    score: float
    confidence: float = 0.0
    bibtex_entry: str
    file_path: str = ""
    doi: str = ""
    passage: Optional[Passage] = None
    kind: str = "cite"
    contradiction: str = ""


class SuggestRequest(BaseModel):
    text: str
    n: int = 5
    workspace_path: str = ""
    precise: Optional[bool] = None


class SuggestResponse(BaseModel):
    citations: List[CitationResult]


class AssistRequest(BaseModel):
    text: str
    workspace_path: str = ""
    cited_keys: List[str] = []
    threshold: Optional[float] = None
    document_path: str = ""


class AssistItem(BaseModel):
    start: int
    end: int
    sentence: str
    claim: bool
    already_cited: bool
    suggestions: List[CitationResult]


class AssistResponse(BaseModel):
    items: List[AssistItem]


class PaperInfo(BaseModel):
    bibtex_key: str
    title: str
    authors: str
    year: str
    file_path: str
    bibtex_entry: str
    doi: str = ""
    from_bib: bool = False


class SearchResult(BaseModel):
    bibtex_key: str
    title: str
    authors: str
    year: str
    excerpt: str
    chunk_index: int
    file_path: str
    page: Optional[int] = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    results: List[SearchResult]


class IngestResponse(BaseModel):
    status: str
    bibtex_key: str
    title: str
    chunks_indexed: int


class FolderIngestRequest(BaseModel):
    path: str


class FolderIngestResult(BaseModel):
    indexed: int
    skipped: int
    errors: List[str]


class ReindexResult(BaseModel):
    total: int
    errors: List[str]

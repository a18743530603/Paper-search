from dataclasses import dataclass
from typing import Optional


STATUS_AVAILABLE = "available"
STATUS_DOWNLOADING = "downloading"
STATUS_DOWNLOADED = "downloaded"
STATUS_LINK_ONLY = "link_only"
STATUS_FAILED = "failed"

PARSE_NOT_STARTED = "not_started"
PARSE_PARSING = "parsing"
PARSE_PARSED = "parsed"
PARSE_FAILED = "parse_failed"

INDEX_NOT_STARTED = "not_indexed"
INDEX_INDEXING = "indexing"
INDEX_INDEXED = "indexed"
INDEX_FAILED = "index_failed"

RAG_ANSWERING = "answering"
RAG_ANSWERED = "answered"
RAG_FAILED = "answer_failed"


@dataclass(frozen=True)
class PaperCandidate:
    title: str
    authors: str
    summary: str
    published: str
    source: str
    publisher: str
    doi: Optional[str]
    page_url: str
    pdf_url: Optional[str]
    status: str


@dataclass(frozen=True)
class PaperChunk:
    page_number: int
    chunk_index: int
    content: str

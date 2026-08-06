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

RESEARCH_TASK_AWAITING = "awaiting_confirmation"
RESEARCH_TASK_RUNNING = "running"
RESEARCH_TASK_AWAITING_SELECTION = "awaiting_selection"
RESEARCH_TASK_PAPERS_SELECTED = "papers_selected"
RESEARCH_TASK_COMPLETED = "completed"
RESEARCH_TASK_FAILED = "failed"
RESEARCH_TASK_CANCELLED = "cancelled"
MAX_RESEARCH_SELECTED_PAPERS = 5

RESEARCH_REPORT_NOT_STARTED = "not_started"
RESEARCH_REPORT_PREPARING = "preparing"
RESEARCH_REPORT_RETRIEVING = "retrieving"
RESEARCH_REPORT_GENERATING = "generating"
RESEARCH_REPORT_COMPLETED = "completed"
RESEARCH_REPORT_FAILED = "failed"
RESEARCH_REPORT_RUNNING = {
    RESEARCH_REPORT_PREPARING,
    RESEARCH_REPORT_RETRIEVING,
    RESEARCH_REPORT_GENERATING,
}

CHUNK_ROLE_RETRIEVAL = "retrieval"
CHUNK_ROLE_PARENT = "parent"
CHUNK_ROLE_CHILD = "child"


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
    parent_index: Optional[int] = None
    role: str = CHUNK_ROLE_RETRIEVAL


@dataclass(frozen=True)
class PaperParentChunk:
    page_number: int
    parent_index: int
    content: str

from dataclasses import dataclass
from typing import Optional


STATUS_DOWNLOADING = "downloading"
STATUS_DOWNLOADED = "downloaded"
STATUS_LINK_ONLY = "link_only"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class PaperCandidate:
    title: str
    authors: str
    summary: str
    published: str
    source: str
    doi: Optional[str]
    page_url: str
    pdf_url: Optional[str]
    status: str


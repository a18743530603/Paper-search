from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PaperChunkEvidence:
    """Raw persisted child-chunk evidence shared by Research Chat tools."""

    evidence_id: str
    paper_id: int
    paper_title: str
    chunk_id: int
    chunk_index: int
    parent_chunk_id: int | None
    section_title: str
    section_path: str
    physical_page: int | None
    content: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "parent_chunk_id": self.parent_chunk_id,
            "section_title": self.section_title,
            "section_path": self.section_path,
            "physical_page": self.physical_page,
            "content": self.content,
        }

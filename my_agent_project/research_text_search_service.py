from dataclasses import dataclass
from typing import Any, Literal

from .context_service import normalize_evidence_text
from .db import (
    get_document,
    get_paper,
    list_paper_chunks_for_text_match,
)
from .research_tool_models import PaperChunkEvidence
from .schemas import PARSE_PARSED
from .section_evidence_service import section_leaf


FIND_IN_PAPER_TOOL_NAME = "find_in_paper"
FindInPaperStatus = Literal[
    "ok",
    "no_match",
    "paper_not_found",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class FindInPaperObservation:
    tool_name: str
    paper_id: int
    query: str
    status: FindInPaperStatus
    evidence: list[PaperChunkEvidence]
    access_limitations: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "paper_id": self.paper_id,
            "query": self.query,
            "status": self.status,
            "evidence": [item.as_dict() for item in self.evidence],
            "access_limitations": self.access_limitations,
            "diagnostics": self.diagnostics,
        }


def _diagnostics(
    *,
    source_chunk_count: int = 0,
    searchable_chunk_count: int = 0,
    matched_chunk_count: int = 0,
) -> dict[str, Any]:
    return {
        "matching_strategy": "normalized_literal_substring_v1",
        "normalization": "NFKC+whitespace_collapse+casefold",
        "case_sensitive": False,
        "source_chunk_count": source_chunk_count,
        "searchable_chunk_count": searchable_chunk_count,
        "matched_chunk_count": matched_chunk_count,
        "returned_evidence_count": matched_chunk_count,
        "ordering_source": "paper_chunks.chunk_index",
        "content_source": "paper_chunks.content",
        "parent_expansion": False,
    }


def _observation(
    *,
    paper_id: int,
    query: str,
    status: FindInPaperStatus,
    evidence: list[PaperChunkEvidence] | None = None,
    limitation_reason: str | None = None,
    source_chunk_count: int = 0,
    searchable_chunk_count: int = 0,
) -> FindInPaperObservation:
    resolved_evidence = evidence or []
    limitations = []
    if limitation_reason:
        limitations.append(
            {
                "paper_id": paper_id,
                "reason": limitation_reason,
            }
        )
    return FindInPaperObservation(
        tool_name=FIND_IN_PAPER_TOOL_NAME,
        paper_id=paper_id,
        query=query,
        status=status,
        evidence=resolved_evidence,
        access_limitations=limitations,
        diagnostics=_diagnostics(
            source_chunk_count=source_chunk_count,
            searchable_chunk_count=searchable_chunk_count,
            matched_chunk_count=len(resolved_evidence),
        ),
    )


def _optional_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def find_paper_text(
    paper_id: int,
    query: str,
) -> FindInPaperObservation:
    """Locate raw child chunks using deterministic normalized substring matching."""

    if paper_id <= 0:
        raise ValueError("paper_id必须为正整数")
    if query is None:
        raise ValueError("query不能为空")
    original_query = str(query).strip()
    normalized_query = normalize_evidence_text(original_query)
    if not normalized_query:
        raise ValueError("query不能为空")

    paper = get_paper(paper_id)
    if paper is None:
        return _observation(
            paper_id=paper_id,
            query=original_query,
            status="paper_not_found",
            limitation_reason="论文记录不存在",
        )

    document = get_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        return _observation(
            paper_id=paper_id,
            query=original_query,
            status="unavailable",
            limitation_reason="论文尚未完成可用解析，当前无法可靠读取正文",
        )

    rows = list_paper_chunks_for_text_match(paper_id)
    paper_title = str(paper["title"] or "")
    evidence: list[PaperChunkEvidence] = []
    searchable_chunk_count = 0
    for row in rows:
        content = str(row.get("content") or "")
        normalized_content = normalize_evidence_text(content)
        if not normalized_content:
            continue
        searchable_chunk_count += 1
        if normalized_query not in normalized_content:
            continue
        try:
            chunk_id = int(row["id"])
            chunk_index = int(row["chunk_index"])
        except (KeyError, TypeError, ValueError):
            continue
        section_path = str(row.get("section_title") or "").strip()
        evidence.append(
            PaperChunkEvidence(
                evidence_id=f"paper_chunk:{chunk_id}",
                paper_id=paper_id,
                paper_title=paper_title,
                chunk_id=chunk_id,
                chunk_index=chunk_index,
                parent_chunk_id=_optional_positive_int(row.get("parent_chunk_id")),
                section_title=section_leaf(section_path),
                section_path=section_path,
                physical_page=_optional_positive_int(row.get("page_number")),
                content=content,
            )
        )

    if searchable_chunk_count == 0:
        return _observation(
            paper_id=paper_id,
            query=original_query,
            status="unavailable",
            limitation_reason="论文已解析，但当前没有可可靠读取的正文块",
            source_chunk_count=len(rows),
        )

    evidence.sort(key=lambda item: item.chunk_index)
    if not evidence:
        return _observation(
            paper_id=paper_id,
            query=original_query,
            status="no_match",
            source_chunk_count=len(rows),
            searchable_chunk_count=searchable_chunk_count,
        )

    return _observation(
        paper_id=paper_id,
        query=original_query,
        status="ok",
        evidence=evidence,
        source_chunk_count=len(rows),
        searchable_chunk_count=searchable_chunk_count,
    )

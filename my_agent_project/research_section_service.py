from dataclasses import dataclass
from typing import Any, Literal

from .db import (
    get_document,
    get_paper,
    list_exact_section_chunks,
    list_paper_section_metadata,
)
from .schemas import PARSE_PARSED
from .section_evidence_service import section_leaf
from .research_tool_models import PaperChunkEvidence


LIST_SECTIONS_TOOL_NAME = "list_sections"
SectionListStatus = Literal["ok", "unavailable", "not_found"]
READ_SECTION_TOOL_NAME = "read_section"
SectionReadStatus = Literal[
    "ok",
    "section_not_found",
    "paper_not_found",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class PaperSection:
    section_title: str
    section_path: str | None
    order: int
    start_page: int | None
    end_page: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "section_title": self.section_title,
            "section_path": self.section_path,
            "order": self.order,
            "start_page": self.start_page,
            "end_page": self.end_page,
        }


@dataclass(frozen=True, slots=True)
class SectionListObservation:
    tool_name: str
    paper_id: int
    status: SectionListStatus
    sections: list[PaperSection]
    access_limitations: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "paper_id": self.paper_id,
            "status": self.status,
            "sections": [section.as_dict() for section in self.sections],
            "access_limitations": self.access_limitations,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True, slots=True)
class SectionReadObservation:
    tool_name: str
    paper_id: int
    section_path: str
    status: SectionReadStatus
    evidence: list[PaperChunkEvidence]
    access_limitations: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "paper_id": self.paper_id,
            "section_path": self.section_path,
            "status": self.status,
            "evidence": [item.as_dict() for item in self.evidence],
            "access_limitations": self.access_limitations,
            "diagnostics": self.diagnostics,
        }


def _diagnostics(
    *,
    source_chunk_count: int = 0,
    recovered_section_count: int = 0,
) -> dict[str, Any]:
    return {
        "source_chunk_count": source_chunk_count,
        "recovered_section_count": recovered_section_count,
        "ordering_source": "paper_chunks.chunk_index",
        "page_range_source": "paper_chunks.page_number_exact_path_bounds",
    }


def _unavailable(
    paper_id: int,
    reason: str,
    *,
    paper_title: str = "",
    source_chunk_count: int = 0,
) -> SectionListObservation:
    limitation: dict[str, Any] = {"paper_id": paper_id, "reason": reason}
    if paper_title:
        limitation["paper_title"] = paper_title
    return SectionListObservation(
        tool_name=LIST_SECTIONS_TOOL_NAME,
        paper_id=paper_id,
        status="unavailable",
        sections=[],
        access_limitations=[limitation],
        diagnostics=_diagnostics(source_chunk_count=source_chunk_count),
    )


def collect_paper_sections(paper_id: int) -> SectionListObservation:
    """Aggregate persisted, reliably navigable section metadata for one paper."""

    if paper_id <= 0:
        raise ValueError("paper_id必须为正整数")

    paper = get_paper(paper_id)
    if paper is None:
        return SectionListObservation(
            tool_name=LIST_SECTIONS_TOOL_NAME,
            paper_id=paper_id,
            status="not_found",
            sections=[],
            access_limitations=[
                {"paper_id": paper_id, "reason": "论文记录不存在"}
            ],
            diagnostics=_diagnostics(),
        )

    paper_title = str(paper["title"] or "")
    document = get_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        return _unavailable(
            paper_id,
            "论文尚未完成可用解析，当前没有可靠的章节导航结构",
            paper_title=paper_title,
        )

    rows = list_paper_section_metadata(paper_id)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        section_path = str(row.get("section_title") or "").strip()
        if not section_path:
            continue
        try:
            chunk_index = int(row["chunk_index"])
        except (KeyError, TypeError, ValueError):
            continue

        group = groups.setdefault(
            section_path,
            {
                "first_chunk_index": chunk_index,
                "pages": [],
            },
        )
        group["first_chunk_index"] = min(
            int(group["first_chunk_index"]),
            chunk_index,
        )
        try:
            page_number = int(row["page_number"])
        except (KeyError, TypeError, ValueError):
            continue
        if page_number > 0:
            group["pages"].append(page_number)

    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (int(item[1]["first_chunk_index"]), item[0]),
    )
    sections = [
        PaperSection(
            section_title=section_leaf(section_path),
            section_path=section_path,
            order=order,
            start_page=min(group["pages"]) if group["pages"] else None,
            end_page=max(group["pages"]) if group["pages"] else None,
        )
        for order, (section_path, group) in enumerate(ordered_groups, start=1)
        if section_leaf(section_path)
    ]
    if not sections:
        return _unavailable(
            paper_id,
            "论文已解析，但当前持久化数据中没有可可靠导航的章节结构",
            paper_title=paper_title,
            source_chunk_count=len(rows),
        )

    return SectionListObservation(
        tool_name=LIST_SECTIONS_TOOL_NAME,
        paper_id=paper_id,
        status="ok",
        sections=sections,
        access_limitations=[],
        diagnostics=_diagnostics(
            source_chunk_count=len(rows),
            recovered_section_count=len(sections),
        ),
    )


def _read_diagnostics(
    *,
    matched_chunk_count: int = 0,
    returned_evidence_count: int = 0,
) -> dict[str, Any]:
    return {
        "matched_chunk_count": matched_chunk_count,
        "returned_evidence_count": returned_evidence_count,
        "matching_strategy": "canonical_path_binary_exact",
        "ordering_source": "paper_chunks.chunk_index",
        "content_source": "paper_chunks.content",
    }


def _read_observation(
    *,
    paper_id: int,
    section_path: str,
    status: SectionReadStatus,
    evidence: list[PaperChunkEvidence] | None = None,
    limitation_reason: str | None = None,
    matched_chunk_count: int = 0,
) -> SectionReadObservation:
    limitations = []
    if limitation_reason:
        limitations.append(
            {
                "paper_id": paper_id,
                "section_path": section_path,
                "reason": limitation_reason,
            }
        )
    resolved_evidence = evidence or []
    return SectionReadObservation(
        tool_name=READ_SECTION_TOOL_NAME,
        paper_id=paper_id,
        section_path=section_path,
        status=status,
        evidence=resolved_evidence,
        access_limitations=limitations,
        diagnostics=_read_diagnostics(
            matched_chunk_count=matched_chunk_count,
            returned_evidence_count=len(resolved_evidence),
        ),
    )


def _optional_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def read_paper_section(
    paper_id: int,
    section_path: str,
) -> SectionReadObservation:
    """Read raw child evidence for one exact persisted section path."""

    if paper_id <= 0:
        raise ValueError("paper_id必须为正整数")
    canonical_path = str(section_path or "").strip()
    if not canonical_path:
        raise ValueError("section_path不能为空")

    paper = get_paper(paper_id)
    if paper is None:
        return _read_observation(
            paper_id=paper_id,
            section_path=canonical_path,
            status="paper_not_found",
            limitation_reason="论文记录不存在",
        )

    document = get_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        return _read_observation(
            paper_id=paper_id,
            section_path=canonical_path,
            status="unavailable",
            limitation_reason="论文尚未完成可用解析，当前无法可靠读取章节正文",
        )

    rows = list_exact_section_chunks(paper_id, canonical_path)
    if not rows:
        return _read_observation(
            paper_id=paper_id,
            section_path=canonical_path,
            status="section_not_found",
            limitation_reason="指定的精确章节路径不存在",
        )

    paper_title = str(paper["title"] or "")
    evidence: list[PaperChunkEvidence] = []
    for row in rows:
        content = str(row.get("content") or "")
        if not content.strip():
            continue
        try:
            chunk_id = int(row["id"])
            chunk_index = int(row["chunk_index"])
        except (KeyError, TypeError, ValueError):
            continue
        persisted_path = str(row.get("section_title") or "").strip()
        if persisted_path != canonical_path:
            continue
        evidence.append(
            PaperChunkEvidence(
                evidence_id=f"paper_chunk:{chunk_id}",
                paper_id=paper_id,
                paper_title=paper_title,
                chunk_id=chunk_id,
                chunk_index=chunk_index,
                parent_chunk_id=_optional_positive_int(row.get("parent_chunk_id")),
                section_title=section_leaf(persisted_path),
                section_path=persisted_path,
                physical_page=_optional_positive_int(row.get("page_number")),
                content=content,
            )
        )

    if not evidence:
        return _read_observation(
            paper_id=paper_id,
            section_path=canonical_path,
            status="unavailable",
            limitation_reason="章节路径存在，但当前没有可可靠读取的正文块",
            matched_chunk_count=len(rows),
        )

    evidence.sort(key=lambda item: item.chunk_index)
    return _read_observation(
        paper_id=paper_id,
        section_path=canonical_path,
        status="ok",
        evidence=evidence,
        matched_chunk_count=len(rows),
    )

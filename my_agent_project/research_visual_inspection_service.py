import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

import pymupdf

from .config import RESEARCH_VISUALS_DIR, ensure_runtime_dirs
from .db import get_document, get_paper, get_paper_visual_chunk
from .pdf_service import pdf_source_hash
from .research_tool_models import PaperChunkEvidence
from .research_visual_service import (
    VISUAL_CONTENT_TYPES,
    VisualAsset,
    get_visual_asset,
    make_visual_id,
    register_visual_asset,
)
from .schemas import PARSE_PARSED
from .section_evidence_service import section_leaf


INSPECT_TABLES_FIGURES_TOOL_NAME = "inspect_tables_figures"
VisualInspectionStatus = Literal[
    "ok",
    "visual_not_found",
    "paper_not_found",
    "unavailable",
]

BBOX_COORDINATE_MAX = 1000.0
BBOX_PADDING = 12.0
RENDER_SCALE = 3.0


@dataclass(frozen=True, slots=True)
class VisualInspectionObservation:
    tool_name: str
    paper_id: int
    chunk_id: int
    status: VisualInspectionStatus
    visual_id: str | None
    content_type: str | None
    physical_page: int | None
    section_title: str
    section_path: str
    text_evidence: PaperChunkEvidence | None
    access_limitations: list[dict[str, Any]]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "paper_id": self.paper_id,
            "chunk_id": self.chunk_id,
            "status": self.status,
            "visual_id": self.visual_id,
            "content_type": self.content_type,
            "physical_page": self.physical_page,
            "section_title": self.section_title,
            "section_path": self.section_path,
            "text_evidence": (
                self.text_evidence.as_dict()
                if self.text_evidence is not None
                else None
            ),
            "access_limitations": self.access_limitations,
            "diagnostics": self.diagnostics,
        }


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_bbox(value: object) -> tuple[float, float, float, float] | None:
    try:
        payload = json.loads(str(value)) if value is not None else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or len(payload) != 4:
        return None
    if not all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(float(item))
        for item in payload
    ):
        return None
    x0, y0, x1, y1 = (float(item) for item in payload)
    if not (
        0.0 <= x0 < x1 <= BBOX_COORDINATE_MAX
        and 0.0 <= y0 < y1 <= BBOX_COORDINATE_MAX
    ):
        return None
    return x0, y0, x1, y1


def _text_evidence(
    paper: Mapping[str, Any],
    row: Mapping[str, Any],
) -> PaperChunkEvidence:
    section_path = str(row.get("section_title") or "").strip()
    return PaperChunkEvidence(
        evidence_id=f"paper_chunk:{int(row['id'])}",
        paper_id=int(row["paper_id"]),
        paper_title=str(paper.get("title") or ""),
        chunk_id=int(row["id"]),
        chunk_index=int(row["chunk_index"]),
        parent_chunk_id=_positive_int(row.get("parent_chunk_id")),
        section_title=section_leaf(section_path),
        section_path=section_path,
        physical_page=_positive_int(row.get("page_number")),
        content=str(row.get("content") or ""),
    )


def _diagnostics(
    *,
    source_hash_verified: bool = False,
    cache_hit: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
    page_rotation: int | None = None,
) -> dict[str, Any]:
    return {
        "visual_id_stability": "current_parse_snapshot",
        "source_hash_verified": source_hash_verified,
        "cache_hit": cache_hit,
        "bbox_coordinate_system": "mineru_0_1000",
        "bbox": list(bbox) if bbox is not None else None,
        "bbox_padding": BBOX_PADDING,
        "render_scale": RENDER_SCALE,
        "page_rotation": page_rotation,
        "output_mime_type": "image/png",
        "parent_expansion": False,
    }


def _observation(
    *,
    paper_id: int,
    chunk_id: int,
    status: VisualInspectionStatus,
    row: Mapping[str, Any] | None = None,
    paper: Mapping[str, Any] | None = None,
    asset: VisualAsset | None = None,
    reason: str | None = None,
    source_hash_verified: bool = False,
    cache_hit: bool = False,
    bbox: tuple[float, float, float, float] | None = None,
    page_rotation: int | None = None,
) -> VisualInspectionObservation:
    section_path = str(row.get("section_title") or "").strip() if row else ""
    evidence = _text_evidence(paper, row) if paper and row else None
    limitations = []
    if reason:
        limitations.append(
            {
                "paper_id": paper_id,
                "chunk_id": chunk_id,
                "reason": reason,
            }
        )
    return VisualInspectionObservation(
        tool_name=INSPECT_TABLES_FIGURES_TOOL_NAME,
        paper_id=paper_id,
        chunk_id=chunk_id,
        status=status,
        visual_id=asset.visual_id if asset is not None else None,
        content_type=(str(row.get("content_type") or "") or None) if row else None,
        physical_page=_positive_int(row.get("page_number")) if row else None,
        section_title=section_leaf(section_path),
        section_path=section_path,
        text_evidence=evidence,
        access_limitations=limitations,
        diagnostics=_diagnostics(
            source_hash_verified=source_hash_verified,
            cache_hit=cache_hit,
            bbox=bbox,
            page_rotation=page_rotation,
        ),
    )


def _normalized_clip(
    page_rect: pymupdf.Rect,
    bbox: tuple[float, float, float, float],
) -> pymupdf.Rect:
    x0, y0, x1, y1 = bbox
    padded_x0 = max(0.0, x0 - BBOX_PADDING)
    padded_y0 = max(0.0, y0 - BBOX_PADDING)
    padded_x1 = min(BBOX_COORDINATE_MAX, x1 + BBOX_PADDING)
    padded_y1 = min(BBOX_COORDINATE_MAX, y1 + BBOX_PADDING)
    return pymupdf.Rect(
        page_rect.x0 + padded_x0 / BBOX_COORDINATE_MAX * page_rect.width,
        page_rect.y0 + padded_y0 / BBOX_COORDINATE_MAX * page_rect.height,
        page_rect.x0 + padded_x1 / BBOX_COORDINATE_MAX * page_rect.width,
        page_rect.y0 + padded_y1 / BBOX_COORDINATE_MAX * page_rect.height,
    )


def _render_png(
    page: pymupdf.Page,
    *,
    bbox: tuple[float, float, float, float],
) -> tuple[Path | None, str | None]:
    clip = _normalized_clip(page.rect, bbox)
    if clip.is_empty or clip.width <= 0 or clip.height <= 0:
        return None, "视觉Chunk bbox无法形成有效裁图区域"

    ensure_runtime_dirs()
    with tempfile.NamedTemporaryFile(
        prefix="visual-crop-",
        suffix=".png",
        dir=RESEARCH_VISUALS_DIR,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE),
            clip=clip,
            alpha=False,
        )
        pixmap.save(temporary_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path, None


def inspect_paper_visual(
    paper_id: int,
    chunk_id: int,
) -> VisualInspectionObservation:
    """Extract one known visual chunk from its matching original PDF snapshot."""

    if paper_id <= 0:
        raise ValueError("paper_id必须为正整数")
    if chunk_id <= 0:
        raise ValueError("chunk_id必须为正整数")

    paper_row = get_paper(paper_id)
    if paper_row is None:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="paper_not_found",
            reason="论文记录不存在",
        )
    paper = dict(paper_row)

    document_row = get_document(paper_id)
    if document_row is None or document_row["parse_status"] != PARSE_PARSED:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            paper=paper,
            reason="论文尚未完成可用解析",
        )
    document = dict(document_row)

    local_path = str(paper.get("local_path") or "").strip()
    source_hash = str(document.get("source_hash") or "").strip()
    if not local_path or not source_hash:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            paper=paper,
            reason="原始PDF或解析source_hash不可用",
        )
    pdf_path = Path(local_path)
    if not pdf_path.is_file():
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            paper=paper,
            reason="原始PDF文件不存在",
        )
    try:
        current_hash = pdf_source_hash(pdf_path)
    except OSError:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            paper=paper,
            reason="原始PDF当前无法读取",
        )
    if current_hash != source_hash:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            paper=paper,
            reason="原始PDF与当前解析快照的source_hash不一致",
        )

    visual_row = get_paper_visual_chunk(paper_id, chunk_id)
    if visual_row is None:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="visual_not_found",
            paper=paper,
            source_hash_verified=True,
            reason="Chunk不存在、不属于当前论文或不是受支持的视觉类型",
        )
    row = dict(visual_row)
    if str(row.get("content_type") or "") not in VISUAL_CONTENT_TYPES:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="visual_not_found",
            row=row,
            paper=paper,
            source_hash_verified=True,
            reason="Chunk不是table、image或chart",
        )

    bbox = _parse_bbox(row.get("bbox_json"))
    page_number = _positive_int(row.get("page_number"))
    document_page_count = _positive_int(document.get("page_count"))
    if bbox is None or page_number is None:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            row=row,
            paper=paper,
            source_hash_verified=True,
            bbox=bbox,
            reason="视觉Chunk缺少合法的物理页码或0-1000 bbox",
        )
    if document_page_count is not None and page_number > document_page_count:
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            row=row,
            paper=paper,
            source_hash_verified=True,
            bbox=bbox,
            reason="视觉Chunk物理页码超出当前解析文档范围",
        )

    try:
        pdf_document = pymupdf.open(pdf_path)
    except (OSError, RuntimeError, pymupdf.FileDataError):
        return _observation(
            paper_id=paper_id,
            chunk_id=chunk_id,
            status="unavailable",
            row=row,
            paper=paper,
            source_hash_verified=True,
            bbox=bbox,
            reason="原始PDF当前无法可靠打开",
        )

    temporary_path: Path | None = None
    with pdf_document:
        page_index = page_number - 1
        if page_index < 0 or page_index >= pdf_document.page_count:
            return _observation(
                paper_id=paper_id,
                chunk_id=chunk_id,
                status="unavailable",
                row=row,
                paper=paper,
                source_hash_verified=True,
                bbox=bbox,
                page_rotation=0,
                reason="视觉Chunk物理页码超出原始PDF范围",
            )
        page = pdf_document.load_page(page_index)
        rotation = int(page.rotation or 0)
        if rotation != 0:
            return _observation(
                paper_id=paper_id,
                chunk_id=chunk_id,
                status="unavailable",
                row=row,
                paper=paper,
                source_hash_verified=True,
                bbox=bbox,
                page_rotation=rotation,
                reason="当前v1不支持非零rotation的PDF页面",
            )

        cached_asset = get_visual_asset(make_visual_id(paper_id, chunk_id))
        if cached_asset is not None:
            return _observation(
                paper_id=paper_id,
                chunk_id=chunk_id,
                status="ok",
                row=row,
                paper=paper,
                asset=cached_asset,
                source_hash_verified=True,
                cache_hit=True,
                bbox=bbox,
                page_rotation=rotation,
            )

        temporary_path, render_error = _render_png(page, bbox=bbox)
        if render_error:
            return _observation(
                paper_id=paper_id,
                chunk_id=chunk_id,
                status="unavailable",
                row=row,
                paper=paper,
                source_hash_verified=True,
                bbox=bbox,
                page_rotation=rotation,
                reason=render_error,
            )

    if temporary_path is None:
        raise RuntimeError("视觉裁图未生成临时PNG")
    try:
        asset = register_visual_asset(
            paper_id,
            chunk_id,
            temporary_path,
            mime_type="image/png",
        )
    finally:
        temporary_path.unlink(missing_ok=True)
    return _observation(
        paper_id=paper_id,
        chunk_id=chunk_id,
        status="ok",
        row=row,
        paper=paper,
        asset=asset,
        source_hash_verified=True,
        cache_hit=False,
        bbox=bbox,
        page_rotation=0,
    )

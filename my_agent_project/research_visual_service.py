import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .config import RESEARCH_VISUALS_DIR
from .db import get_paper_visual_chunk
from .section_evidence_service import section_leaf


VISUAL_CONTENT_TYPES = frozenset({"table", "image", "chart"})
VISUAL_ID_PATTERN = re.compile(
    r"^paper_visual:(?P<paper_id>[1-9]\d*):(?P<chunk_id>[1-9]\d*)$"
)
MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
IMAGE_SIGNATURES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


@dataclass(frozen=True, slots=True)
class VisualAsset:
    visual_id: str
    paper_id: int
    chunk_id: int
    content_type: str
    physical_page: int | None
    section_title: str
    image_path: str
    mime_type: str

    def as_public_payload(self) -> dict[str, Any]:
        return {
            "visual_id": self.visual_id,
            "image_url": f"/research-visuals/{quote(self.visual_id, safe='')}",
            "paper_id": self.paper_id,
            "chunk_id": self.chunk_id,
            "content_type": self.content_type,
            "physical_page": self.physical_page,
            "section_title": self.section_title,
            "mime_type": self.mime_type,
        }


def make_visual_id(paper_id: int, chunk_id: int) -> str:
    if paper_id <= 0 or chunk_id <= 0:
        raise ValueError("paper_id和chunk_id必须为正整数")
    return f"paper_visual:{paper_id}:{chunk_id}"


def parse_visual_id(visual_id: str) -> tuple[int, int]:
    match = VISUAL_ID_PATTERN.fullmatch(str(visual_id or ""))
    if match is None:
        raise ValueError("visual_id格式无效")
    return int(match.group("paper_id")), int(match.group("chunk_id"))


def _safe_asset_path(paper_id: int, chunk_id: int, suffix: str) -> Path:
    root = RESEARCH_VISUALS_DIR.resolve()
    path = (root / f"paper_{paper_id}" / f"chunk_{chunk_id}{suffix}").resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("visual asset路径越界") from exc
    return path


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _asset_from_row(row: Mapping[str, Any], path: Path, mime_type: str) -> VisualAsset:
    paper_id = int(row["paper_id"])
    chunk_id = int(row["id"])
    content_type = str(row.get("content_type") or "")
    if content_type not in VISUAL_CONTENT_TYPES:
        raise ValueError("Chunk不是受支持的视觉类型")
    return VisualAsset(
        visual_id=make_visual_id(paper_id, chunk_id),
        paper_id=paper_id,
        chunk_id=chunk_id,
        content_type=content_type,
        physical_page=_positive_int(row.get("page_number")),
        section_title=section_leaf(row.get("section_title")),
        image_path=str(path),
        mime_type=mime_type,
    )


def _file_matches_mime(path: Path, mime_type: str) -> bool:
    signature = IMAGE_SIGNATURES[mime_type]
    try:
        with path.open("rb") as file:
            return file.read(len(signature)).startswith(signature)
    except OSError:
        return False


def register_visual_asset(
    paper_id: int,
    chunk_id: int,
    source_path: str | Path,
    *,
    mime_type: str = "image/png",
) -> VisualAsset:
    """Register a trusted internal image for an existing visual child chunk."""

    if mime_type not in MIME_SUFFIXES:
        raise ValueError("只支持PNG或JPEG视觉附件")
    row = get_paper_visual_chunk(paper_id, chunk_id)
    if row is None:
        raise LookupError("视觉Chunk不存在或类型不受支持")
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError("视觉附件源文件不存在")
    if not _file_matches_mime(source, mime_type):
        raise ValueError("视觉附件内容与mime_type不匹配")

    destination = _safe_asset_path(paper_id, chunk_id, MIME_SUFFIXES[mime_type])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _file_matches_mime(destination, mime_type):
        return _asset_from_row(row, destination, mime_type)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
    try:
        shutil.copyfile(source, temporary)
        try:
            os.replace(temporary, destination)
        except OSError:
            if not (
                destination.is_file()
                and _file_matches_mime(destination, mime_type)
            ):
                raise
    finally:
        temporary.unlink(missing_ok=True)
    return _asset_from_row(row, destination, mime_type)


def get_visual_asset(visual_id: str) -> VisualAsset | None:
    try:
        paper_id, chunk_id = parse_visual_id(visual_id)
    except ValueError:
        return None
    row = get_paper_visual_chunk(paper_id, chunk_id)
    if row is None:
        return None
    for mime_type, suffix in MIME_SUFFIXES.items():
        path = _safe_asset_path(paper_id, chunk_id, suffix)
        if path.is_file() and _file_matches_mime(path, mime_type):
            return _asset_from_row(row, path, mime_type)
    return None


def visual_assets_from_evidence(
    evidence: Sequence[Mapping[str, Any]],
) -> list[VisualAsset]:
    assets = []
    seen = set()
    for item in evidence:
        visual_id = str(item.get("visual_id") or "")
        if not visual_id or visual_id in seen:
            continue
        asset = get_visual_asset(visual_id)
        if asset is None:
            continue
        if (
            _positive_int(item.get("paper_id")) != asset.paper_id
            or _positive_int(item.get("chunk_id")) != asset.chunk_id
        ):
            continue
        assets.append(asset)
        seen.add(visual_id)
    return assets


def visual_payloads_from_evidence(
    evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [asset.as_public_payload() for asset in visual_assets_from_evidence(evidence)]


def attach_visual_references(
    evidence: Sequence[Mapping[str, Any]],
    assets: Sequence[VisualAsset],
) -> list[dict[str, Any]]:
    """Attach stable visual IDs to matching evidence without rewriting content."""

    by_chunk = {(asset.paper_id, asset.chunk_id): asset.visual_id for asset in assets}
    attached = []
    for item in evidence:
        copied = dict(item)
        key = (_positive_int(copied.get("paper_id")), _positive_int(copied.get("chunk_id")))
        visual_id = by_chunk.get(key)
        if visual_id:
            copied["visual_id"] = visual_id
        attached.append(copied)
    return attached

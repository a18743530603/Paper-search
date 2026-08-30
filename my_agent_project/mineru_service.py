import json
import re
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import (
    MINERU_API_TOKEN,
    MINERU_BASE_URL,
    MINERU_MODEL_VERSION,
    MINERU_PARSE_TIMEOUT,
    MINERU_POLL_INTERVAL,
    MINERU_REQUEST_TIMEOUT,
    MINERU_RESULTS_DIR,
)
from .context_service import estimate_text_tokens
from .schemas import PaperChunk, PaperParentChunk


MINERU_PARSER_BACKEND = "mineru_cloud"
MINERU_CHUNK_STRATEGY = "mineru_structured"
MINERU_PARENT_TARGET_TOKENS = 1000
MINERU_PARENT_HARD_MAX_TOKENS = 1500
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_RESULT_ZIP_BYTES = 512 * 1024 * 1024
MAX_CONTENT_LIST_BYTES = 128 * 1024 * 1024
MAX_MARKDOWN_BYTES = 64 * 1024 * 1024
EXCLUDED_BLOCK_TYPES = {"header", "footer", "page_number", "aside_text"}
EXCLUDED_SECTIONS = {"references", "bibliography", "acknowledgment", "acknowledgments"}
APPENDIX_PATTERN = re.compile(r"^(?:appendix|[a-z][.)]\s+appendix)\b", re.IGNORECASE)

ParseProgressCallback = Callable[[str, str | None, int | None, int | None], None]


class MinerUError(RuntimeError):
    pass


@dataclass(frozen=True)
class MinerUParseResult:
    batch_id: str
    content_list: list[dict[str, Any]]
    markdown: str
    result_dir: Path


def is_mineru_configured() -> bool:
    return bool(MINERU_API_TOKEN)


def _validated_https_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise MinerUError(f"MinerU返回了无效的{label}地址")
    return value


def _api_payload(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise MinerUError("MinerU返回了无法解析的JSON") from exc
    if not isinstance(payload, dict):
        raise MinerUError("MinerU返回格式不正确")
    if payload.get("code") != 0:
        message = str(payload.get("msg") or "MinerU请求失败")
        raise MinerUError(message[:500])
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MinerUError("MinerU响应缺少data字段")
    return data


def _file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            yield chunk


def _submit_pdf(
    client: httpx.Client,
    path: Path,
    data_id: str,
    model_version: str,
) -> tuple[str, str]:
    payload = {
        "files": [
            {
                "name": path.name,
                "data_id": data_id,
                "is_ocr": False,
            }
        ],
        "model_version": model_version,
        "enable_formula": True,
        "enable_table": True,
    }
    data = _api_payload(
        client.post(
            f"{MINERU_BASE_URL}/file-urls/batch",
            headers={"Authorization": f"Bearer {MINERU_API_TOKEN}"},
            json=payload,
        )
    )
    batch_id = str(data.get("batch_id") or "").strip()
    file_urls = data.get("file_urls")
    if not batch_id or not isinstance(file_urls, list) or len(file_urls) != 1:
        raise MinerUError("MinerU没有返回有效的上传任务")
    upload_url = _validated_https_url(str(file_urls[0]), "上传")
    return batch_id, upload_url


def _upload_pdf(client: httpx.Client, path: Path, upload_url: str) -> None:
    response = client.put(upload_url, content=_file_chunks(path))
    response.raise_for_status()


def _find_batch_result(data: Mapping[str, Any], data_id: str) -> dict[str, Any]:
    results = data.get("extract_result")
    if not isinstance(results, list) or not results:
        raise MinerUError("MinerU任务响应缺少解析结果")
    for result in results:
        if isinstance(result, dict) and result.get("data_id") == data_id:
            return result
    if len(results) == 1 and isinstance(results[0], dict):
        return results[0]
    raise MinerUError("MinerU任务响应中找不到当前论文")


def _wait_for_result(
    client: httpx.Client,
    batch_id: str,
    data_id: str,
    on_progress: ParseProgressCallback | None,
) -> str:
    deadline = time.monotonic() + MINERU_PARSE_TIMEOUT
    headers = {"Authorization": f"Bearer {MINERU_API_TOKEN}"}
    while True:
        data = _api_payload(
            client.get(
                f"{MINERU_BASE_URL}/extract-results/batch/{batch_id}",
                headers=headers,
            )
        )
        result = _find_batch_result(data, data_id)
        state = str(result.get("state") or "unknown")
        progress = result.get("extract_progress")
        extracted = total = None
        if isinstance(progress, dict):
            extracted = _optional_int(progress.get("extracted_pages"))
            total = _optional_int(progress.get("total_pages"))
        if on_progress is not None:
            on_progress(state, batch_id, extracted, total)
        if state == "done":
            return _validated_https_url(
                str(result.get("full_zip_url") or ""),
                "结果下载",
            )
        if state == "failed":
            error = str(result.get("err_msg") or "MinerU解析失败")
            raise MinerUError(error[:500])
        if time.monotonic() >= deadline:
            raise MinerUError("MinerU云端解析超时，可稍后从原任务继续重试")
        time.sleep(max(MINERU_POLL_INTERVAL, 0.1))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_cache_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)[:80] or "default"


def result_cache_dir(source_hash: str, model_version: str) -> Path:
    return MINERU_RESULTS_DIR / _safe_cache_key(model_version) / source_hash


def load_cached_content_list(
    source_hash: str,
    model_version: str,
) -> list[dict[str, Any]] | None:
    path = result_cache_dir(source_hash, model_version) / "content_list.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        return None
    return payload


def _selected_zip_entry(
    archive: zipfile.ZipFile,
    *,
    suffix: str,
    maximum_size: int,
) -> zipfile.ZipInfo | None:
    matches = [
        info
        for info in archive.infolist()
        if not info.is_dir() and info.filename.lower().endswith(suffix)
    ]
    if not matches:
        return None
    selected = min(matches, key=lambda info: (len(Path(info.filename).parts), info.filename))
    if selected.file_size > maximum_size:
        raise MinerUError(f"MinerU结果中的{suffix}文件过大")
    return selected


def _read_result_zip(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        with zipfile.ZipFile(path) as archive:
            content_info = _selected_zip_entry(
                archive,
                suffix="_content_list.json",
                maximum_size=MAX_CONTENT_LIST_BYTES,
            )
            if content_info is None:
                content_info = _selected_zip_entry(
                    archive,
                    suffix="content_list.json",
                    maximum_size=MAX_CONTENT_LIST_BYTES,
                )
            if content_info is None:
                raise MinerUError("MinerU结果中缺少content_list.json")
            content_list = json.loads(archive.read(content_info).decode("utf-8"))
            markdown_info = _selected_zip_entry(
                archive,
                suffix="full.md",
                maximum_size=MAX_MARKDOWN_BYTES,
            )
            markdown = (
                archive.read(markdown_info).decode("utf-8")
                if markdown_info is not None
                else ""
            )
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinerUError("MinerU结果压缩包损坏或结构不正确") from exc
    if not isinstance(content_list, list) or not all(
        isinstance(item, dict) for item in content_list
    ):
        raise MinerUError("MinerU content_list.json不是有效的内容块列表")
    return content_list, markdown


def _download_result_zip(
    client: httpx.Client,
    zip_url: str,
    target: Path,
) -> None:
    received = 0
    with client.stream("GET", zip_url) as response:
        response.raise_for_status()
        with target.open("wb") as file:
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > MAX_RESULT_ZIP_BYTES:
                    raise MinerUError("MinerU结果压缩包超过安全大小限制")
                file.write(chunk)


def parse_pdf_with_mineru(
    path: Path,
    *,
    source_hash: str,
    data_id: str,
    existing_batch_id: str | None = None,
    on_progress: ParseProgressCallback | None = None,
    client: httpx.Client | None = None,
) -> MinerUParseResult:
    if not is_mineru_configured():
        raise MinerUError("未配置MINERU_API_TOKEN，无法调用MinerU精准解析API")
    if not path.is_file():
        raise MinerUError("待解析PDF不存在")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise MinerUError("PDF超过MinerU精准解析API的200MB限制")
    if MINERU_MODEL_VERSION not in {"pipeline", "vlm"}:
        raise MinerUError("MINERU_MODEL_VERSION只能是pipeline或vlm")

    owned_client = client is None
    active_client = client or httpx.Client(
        timeout=MINERU_REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    batch_id = existing_batch_id
    temporary_zip: Path | None = None
    try:
        if not batch_id:
            batch_id, upload_url = _submit_pdf(
                active_client,
                path,
                data_id,
                MINERU_MODEL_VERSION,
            )
            if on_progress is not None:
                on_progress("uploading", batch_id, None, None)
            _upload_pdf(active_client, path, upload_url)
            if on_progress is not None:
                on_progress("pending", batch_id, None, None)

        zip_url = _wait_for_result(active_client, batch_id, data_id, on_progress)
        directory = result_cache_dir(source_hash, MINERU_MODEL_VERSION)
        directory.mkdir(parents=True, exist_ok=True)
        temporary_zip = directory / "result.zip.part"
        _download_result_zip(active_client, zip_url, temporary_zip)
        content_list, markdown = _read_result_zip(temporary_zip)
        (directory / "content_list.json").write_text(
            json.dumps(content_list, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if markdown:
            (directory / "full.md").write_text(markdown, encoding="utf-8")
        temporary_zip.unlink(missing_ok=True)
        return MinerUParseResult(batch_id, content_list, markdown, directory)
    finally:
        if temporary_zip is not None:
            temporary_zip.unlink(missing_ok=True)
        if owned_client:
            active_client.close()


def _clean_text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"[ \t]+", " ", value).strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "\n".join(
            text for item in value if (text := _clean_text(item))
        )
    return ""


def _block_text(block: Mapping[str, Any], block_type: str) -> str:
    if block_type in {"text", "header", "footer", "page_number", "aside_text", "page_footnote"}:
        return _clean_text(block.get("text"))
    if block_type == "table":
        return _clean_text(
            [block.get("table_caption"), block.get("table_body"), block.get("table_footnote")]
        )
    if block_type in {"image", "chart"}:
        return _clean_text(
            [
                block.get("img_caption"),
                block.get("image_caption"),
                block.get("chart_caption"),
                block.get("content"),
                block.get("img_footnote"),
                block.get("image_footnote"),
                block.get("chart_footnote"),
            ]
        )
    if block_type == "equation":
        return _clean_text(block.get("text") or block.get("equation"))
    if block_type == "code":
        return _clean_text(
            [block.get("code_caption"), block.get("code_body"), block.get("code_footnote")]
        )
    if block_type == "list":
        return _clean_text(block.get("list_items"))
    return _clean_text(block.get("text") or block.get("content"))


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def build_structured_chunks(
    content_list: Sequence[Mapping[str, Any]],
) -> tuple[int, list[PaperChunk], list[PaperParentChunk], dict[str, Any]]:
    sections: list[str] = []
    chunks: list[PaperChunk] = []
    content_type_counts: dict[str, int] = {}
    section_paths: set[str] = set()
    excluded_section = False
    maximum_page = 0
    skipped_blocks = 0

    for block in content_list:
        block_type = str(block.get("type") or "text").strip().lower()
        page_idx = _optional_int(block.get("page_idx"))
        page_number = max((page_idx or 0) + 1, 1)
        maximum_page = max(maximum_page, page_number)
        text = _block_text(block, block_type)

        text_level = _optional_int(block.get("text_level"))
        if block_type == "text" and text and text_level and text_level > 0:
            level = max(1, min(text_level, 8))
            normalized = text.casefold().strip(" .:")
            is_appendix = bool(APPENDIX_PATTERN.match(normalized))
            if excluded_section and not is_appendix:
                skipped_blocks += 1
                continue
            sections = sections[: level - 1]
            sections.append(text)
            excluded_section = normalized in EXCLUDED_SECTIONS
            if is_appendix:
                excluded_section = False
            if not excluded_section:
                section_paths.add(" > ".join(sections))
            continue

        if block_type in EXCLUDED_BLOCK_TYPES or excluded_section or not text:
            skipped_blocks += 1
            continue
        normalized_type = "footnote" if block_type == "page_footnote" else block_type
        if normalized_type not in {
            "text",
            "table",
            "equation",
            "code",
            "list",
            "image",
            "chart",
            "footnote",
        }:
            normalized_type = "text"
        section_title = " > ".join(sections)
        chunks.append(
            PaperChunk(
                page_number=page_number,
                chunk_index=len(chunks),
                content=text,
                section_title=section_title,
                content_type=normalized_type,
                bbox=_bbox(block.get("bbox")),
            )
        )
        content_type_counts[normalized_type] = content_type_counts.get(normalized_type, 0) + 1

    chunks, parent_chunks, parent_stats = build_structured_parent_chunks(chunks)
    stats = {
        "source_block_count": len(content_list),
        "structured_chunk_count": len(chunks),
        "section_count": len(section_paths),
        "skipped_block_count": skipped_blocks,
        "content_type_counts": content_type_counts,
        **parent_stats,
    }
    return maximum_page, chunks, parent_chunks, stats


def build_structured_parent_chunks(
    chunks: Sequence[PaperChunk],
    *,
    target_tokens: int = MINERU_PARENT_TARGET_TOKENS,
    hard_max_tokens: int = MINERU_PARENT_HARD_MAX_TOKENS,
) -> tuple[list[PaperChunk], list[PaperParentChunk], dict[str, int]]:
    """Group consecutive MinerU blocks into page- and section-bounded parents."""

    if target_tokens < 1:
        raise ValueError("target_tokens must be positive")
    if hard_max_tokens < target_tokens:
        raise ValueError(
            "hard_max_tokens must be greater than or equal to target_tokens"
        )

    linked_chunks: list[PaperChunk] = []
    parents: list[PaperParentChunk] = []
    current: list[PaperChunk] = []
    current_key: tuple[int, str] | None = None
    oversized_parent_count = 0
    parent_token_counts: list[int] = []

    def parent_text(items: Sequence[PaperChunk]) -> str:
        return "\n\n".join(item.content for item in items)

    def flush() -> None:
        nonlocal current, current_key, oversized_parent_count
        if not current:
            return
        content = parent_text(current)
        estimated_tokens = estimate_text_tokens(content)
        parent_index = len(parents)
        parents.append(
            PaperParentChunk(
                page_number=current[0].page_number,
                parent_index=parent_index,
                content=content,
            )
        )
        linked_chunks.extend(
            replace(chunk, parent_index=parent_index) for chunk in current
        )
        parent_token_counts.append(estimated_tokens)
        if estimated_tokens > hard_max_tokens:
            # A MinerU block is atomic: preserve its original content and retrieval
            # granularity instead of truncating or splitting it solely for a parent.
            oversized_parent_count += 1
        current = []
        current_key = None

    for chunk in chunks:
        chunk_key = (chunk.page_number, chunk.section_title)
        if current and chunk_key != current_key:
            flush()

        candidate = [*current, chunk]
        if current and estimate_text_tokens(parent_text(candidate)) > hard_max_tokens:
            flush()
            candidate = [chunk]

        current = candidate
        current_key = chunk_key
        if estimate_text_tokens(parent_text(current)) >= target_tokens:
            flush()

    flush()
    return linked_chunks, parents, {
        "parent_chunk_count": len(parents),
        "parent_target_tokens": target_tokens,
        "parent_hard_max_tokens": hard_max_tokens,
        "parent_max_estimated_tokens": max(parent_token_counts, default=0),
        "oversized_parent_count": oversized_parent_count,
    }

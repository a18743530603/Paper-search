import re
from pathlib import Path

from .db import get_paper, replace_paper_chunks, update_parse_status
from .schemas import PARSE_FAILED, PARSE_PARSING, PaperChunk


DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150


def normalize_pdf_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_page_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    text = normalize_pdf_text(text)
    if not text:
        return []
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(start + chunk_size, len(text))
        end = hard_end
        if hard_end < len(text):
            search_from = start + chunk_size // 2
            candidates = [
                text.rfind(marker, search_from, hard_end)
                for marker in ("\n\n", ". ", "。", "; ", " ")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1

        content = text[start:end].strip()
        if content:
            chunks.append(content)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def extract_pdf_chunks(
    path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[int, list[PaperChunk]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf 依赖，请先执行 uv sync") from exc

    reader = PdfReader(str(path))
    chunks: list[PaperChunk] = []
    chunk_index = 0
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        for content in split_page_text(
            page_text,
            chunk_size=chunk_size,
            overlap=overlap,
        ):
            chunks.append(
                PaperChunk(
                    page_number=page_number,
                    chunk_index=chunk_index,
                    content=content,
                )
            )
            chunk_index += 1
    return len(reader.pages), chunks


def parse_paper_pdf(
    paper_id: int,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> None:
    paper = get_paper(paper_id)
    if paper is None:
        return

    update_parse_status(paper_id, PARSE_PARSING)
    try:
        local_path = paper["local_path"]
        if not local_path:
            raise RuntimeError("当前论文没有可解析的本地 PDF")
        path = Path(local_path)
        if not path.is_file():
            raise RuntimeError("本地 PDF 文件不存在")

        page_count, chunks = extract_pdf_chunks(
            path,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        if not chunks:
            raise RuntimeError("PDF 未提取到可用文本，文件可能是扫描版")
        replace_paper_chunks(paper_id, chunks, page_count=page_count)
    except Exception as exc:
        update_parse_status(paper_id, PARSE_FAILED, error=str(exc)[:500])

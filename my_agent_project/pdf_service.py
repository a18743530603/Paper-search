import re
from pathlib import Path

from .db import get_paper, replace_paper_chunks, update_parse_status
from .schemas import PARSE_FAILED, PARSE_PARSING, PaperChunk


DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150
CHUNK_STRATEGY_LENGTH = "length_boundary"
CHUNK_STRATEGY_ACADEMIC = "academic_section"
SUPPORTED_CHUNK_STRATEGIES = {CHUNK_STRATEGY_LENGTH, CHUNK_STRATEGY_ACADEMIC}

NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?P<number>[0-9]+(?:\.[0-9]+)*)(?P<marker>[.)])?\s+"
    r"(?P<title>[A-Z].{1,100})$"
)
APPENDIX_HEADING_PATTERN = re.compile(
    r"^(?P<number>[A-Z](?:\.[0-9]+)+)[.)]?\s+(?P<title>[A-Z].{1,100})$"
)
ROMAN_HEADING_PATTERN = re.compile(
    r"^(?P<number>I|II|III|IV|V|VI|VII|VIII|IX|X)[.)]\s+"
    r"(?P<title>[A-Z].{1,100})$"
)
LETTER_HEADING_PATTERN = re.compile(
    r"^(?P<number>[A-Z])[.)]\s+(?P<title>[A-Z].{1,100})$"
)
WRAPPED_PREFIX_PATTERN = re.compile(r"^(?:[IVXLC]+|[A-Z])[.)]\s+[A-Z]$")
TRAILING_HEADING_STOPWORDS = {"a", "an", "and", "for", "of", "or", "the", "to", "two"}
COMMON_SECTION_TITLES = {
    "abstract",
    "introduction",
    "background",
    "related work",
    "literature review",
    "method",
    "methods",
    "methodology",
    "materials and methods",
    "proposed method",
    "experiments",
    "experimental setup",
    "experimental settings",
    "implementation details",
    "results",
    "main results",
    "results and discussion",
    "discussion",
    "ablation study",
    "analysis",
    "limitations",
    "conclusion",
    "conclusions",
    "acknowledgments",
    "acknowledgements",
    "acknowledgment",
    "references",
    "bibliography",
    "appendix",
}
EXCLUDED_SECTION_TITLES = {
    "acknowledgments",
    "acknowledgements",
    "acknowledgment",
    "references",
    "bibliography",
}


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


def detect_section_heading(line: str) -> tuple[int, str] | None:
    candidate = re.sub(r"\s+", " ", line).strip().strip(":")
    if not candidate or len(candidate) > 120:
        return None
    if candidate.lower().startswith(("figure ", "fig. ", "table ", "algorithm ")):
        return None

    appendix = APPENDIX_HEADING_PATTERN.fullmatch(candidate)
    if appendix:
        number = appendix.group("number")
        title = appendix.group("title").strip()
        if len(title.split()) <= 14 and not title.endswith((",", ";", ".", "?", "!")):
            return number.count(".") + 1, f"{number} {title}"

    numbered = NUMBERED_HEADING_PATTERN.fullmatch(candidate)
    if numbered:
        number = numbered.group("number")
        title = numbered.group("title").strip()
        marker = numbered.group("marker")
        if (
            marker != ")"
            and len(title.split()) <= 14
            and not (marker is None and re.search(r"[0-9]", title))
            and title.split()[-1].lower() not in TRAILING_HEADING_STOPWORDS
            and not re.search(r":\s*(?:I|Once|Our|The|This|We)\b", title)
            and not title.endswith((",", ";", ".", "?", "!"))
        ):
            return number.count(".") + 1, f"{number} {title}"

    roman = ROMAN_HEADING_PATTERN.fullmatch(candidate)
    if roman:
        title = roman.group("title").strip()
        if len(title.split()) <= 14 and not title.endswith((",", ";", ".", "?", "!")):
            return 1, f"{roman.group('number').upper()} {title}"

    letter = LETTER_HEADING_PATTERN.fullmatch(candidate)
    if letter:
        title = letter.group("title").strip()
        if len(title.split()) <= 14 and not title.endswith((",", ";", ".", "?", "!")):
            level = 1 if title.lower() == "appendix" else 2
            return level, f"{letter.group('number')} {title}"

    normalized = candidate.lower()
    if normalized in COMMON_SECTION_TITLES:
        return 1, candidate
    return None


def _merge_wrapped_heading_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""

        if WRAPPED_PREFIX_PATTERN.fullmatch(line) and next_line:
            combined = f"{line}{next_line}"
            if detect_section_heading(combined) is not None:
                merged.append(combined)
                index += 2
                continue

        if len(line) == 1 and line.isupper() and next_line:
            combined = f"{line}{next_line}"
            if combined.lower() in COMMON_SECTION_TITLES:
                merged.append(combined)
                index += 2
                continue

        merged.append(line)
        index += 1
    return merged


def _update_section_path(
    current_sections: list[str],
    level: int,
    heading: str,
) -> list[str]:
    if (
        level == 1
        and re.match(r"^[0-9]+\s", heading)
        and current_sections
        and (
            re.match(r"^[IVXLC]+\s", current_sections[0])
            or _section_name(current_sections[0]) == "appendix"
        )
    ):
        level = min(len(current_sections) + 1, 3)
    if level <= 1:
        return [heading]
    parent_count = min(level - 1, len(current_sections))
    return [*current_sections[:parent_count], heading]


def _section_name(heading: str) -> str:
    without_number = re.sub(
        r"^(?:\d+(?:\.\d+)*|[A-Z]+)\s+",
        "",
        heading,
        flags=re.IGNORECASE,
    )
    return without_number.strip().lower()


def split_academic_page_text(
    text: str,
    *,
    current_sections: list[str] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[list[str], list[str]]:
    sections = list(current_sections or [])
    normalized = normalize_pdf_text(text)
    if not normalized:
        return [], sections

    segments: list[tuple[list[str], str]] = []
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            segments.append((list(sections), body))
        buffer.clear()

    lines = _merge_wrapped_heading_lines(normalized.splitlines())
    for raw_line in lines:
        line = raw_line.strip()
        heading = detect_section_heading(line)
        if heading is None:
            buffer.append(line)
            continue
        if sections and _section_name(sections[-1]) in EXCLUDED_SECTION_TITLES:
            _, title = heading
            if _section_name(title) != "appendix":
                buffer.append(line)
                continue
        flush()
        level, title = heading
        if _section_name(title) == "appendix":
            sections = [title]
        else:
            sections = _update_section_path(sections, level, title)
    flush()

    chunks: list[str] = []
    for section_path, body in segments:
        if section_path and _section_name(section_path[-1]) in EXCLUDED_SECTION_TITLES:
            continue
        section_label = " > ".join(section_path) if section_path else "Front Matter"
        prefix = f"[Section: {section_label}]"
        body_size = max(120, chunk_size - len(prefix) - 1)
        body_overlap = min(overlap, body_size - 1)
        for body_chunk in split_page_text(
            body,
            chunk_size=body_size,
            overlap=body_overlap,
        ):
            chunks.append(f"{prefix}\n{body_chunk}")
    return chunks, sections


def extract_pdf_chunks(
    path: Path,
    *,
    strategy: str = CHUNK_STRATEGY_LENGTH,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[int, list[PaperChunk]]:
    if strategy not in SUPPORTED_CHUNK_STRATEGIES:
        raise ValueError(f"unsupported chunk strategy: {strategy}")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf 依赖，请先执行 uv sync") from exc

    reader = PdfReader(str(path))
    chunks: list[PaperChunk] = []
    chunk_index = 0
    current_sections: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if strategy == CHUNK_STRATEGY_ACADEMIC:
            page_chunks, current_sections = split_academic_page_text(
                page_text,
                current_sections=current_sections,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        else:
            page_chunks = split_page_text(
                page_text,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        for content in page_chunks:
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
    strategy: str = CHUNK_STRATEGY_LENGTH,
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
            strategy=strategy,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        if not chunks:
            raise RuntimeError("PDF 未提取到可用文本，文件可能是扫描版")
        replace_paper_chunks(paper_id, chunks, page_count=page_count)
    except Exception as exc:
        update_parse_status(paper_id, PARSE_FAILED, error=str(exc)[:500])

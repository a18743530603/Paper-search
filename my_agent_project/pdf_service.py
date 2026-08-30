import hashlib
import json
import math
import re
from pathlib import Path
from typing import Callable, Sequence

from .db import (
    count_paper_parent_chunks,
    get_cached_pdf_pages,
    get_document,
    get_paper,
    replace_cached_pdf_pages,
    replace_paper_chunks,
    update_parse_status,
    update_remote_parse_state,
)
from .config import MINERU_MODEL_VERSION
from .mineru_service import (
    MINERU_CHUNK_STRATEGY,
    MINERU_PARSER_BACKEND,
    build_structured_chunks,
    load_cached_content_list,
    parse_pdf_with_mineru,
)
from .schemas import (
    CHUNK_ROLE_CHILD,
    CHUNK_ROLE_PARENT,
    PARSE_FAILED,
    PARSE_PARSING,
    PaperChunk,
    PaperParentChunk,
)


DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150
CHUNK_STRATEGY_LENGTH = "length_boundary"
CHUNK_STRATEGY_ACADEMIC = "academic_section"
CHUNK_STRATEGY_ACADEMIC_NO_PREFIX = "academic_section_no_prefix"
CHUNK_STRATEGY_PAPER_SENTENCE = "paper_section_sentence"
CHUNK_STRATEGY_PARENT_CHILD = "paper_parent_child"
CHUNK_STRATEGY_MULTI_GRANULARITY = "paper_multi_granularity"
CHUNK_STRATEGY_TABLE_CONTEXT = "paper_table_context"
CHUNK_STRATEGY_SEMANTIC_LOCAL = "paper_semantic_breakpoint"
CHUNK_STRATEGY_SEMANTIC = "paper_semantic_breakpoint_global"
SUPPORTED_CHUNK_STRATEGIES = {
    CHUNK_STRATEGY_LENGTH,
    CHUNK_STRATEGY_ACADEMIC,
    CHUNK_STRATEGY_ACADEMIC_NO_PREFIX,
    CHUNK_STRATEGY_PAPER_SENTENCE,
    CHUNK_STRATEGY_PARENT_CHILD,
    CHUNK_STRATEGY_MULTI_GRANULARITY,
    CHUNK_STRATEGY_TABLE_CONTEXT,
    CHUNK_STRATEGY_SEMANTIC_LOCAL,
    CHUNK_STRATEGY_SEMANTIC,
}

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
SENTENCE_BOUNDARY_PATTERN = re.compile(
    r"(?<=[.!?])\s+(?=(?:[\"'(\[])?[A-Z0-9])"
)
SENTENCE_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "et al.",
    "Fig.",
    "Figs.",
    "Eq.",
    "Eqs.",
    "Sec.",
    "Dr.",
    "Mr.",
    "Mrs.",
    "vs.",
)
PROTECTED_PERIOD = "\u0001"
TABLE_CONTEXT_PREFIX = "[Table Context]"
TABLE_BODY_PREFIX = "[Table Body]"
TABLE_CAPTION_PATTERN = re.compile(
    r"^(?:table|tab\.)\s*(?:[ivxlcdm]+|\d+)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
TABLE_REFERENCE_VERB_PATTERN = re.compile(
    r"^[\s.:]*(?:gives?|shows?|presents?|reveals?|reports?|summari[sz]es?|"
    r"provides?|illustrates?|contains?|compares?|lists?|given)\b",
    re.IGNORECASE,
)
SEMANTIC_UNIT_SIZE = 300
SEMANTIC_BREAKPOINT_PERCENTILE = 25
SemanticEmbedder = Callable[[list[str]], list[list[float]]]


def _increment_cache_stat(
    cache_stats: dict[str, int] | None,
    name: str,
    amount: int = 1,
) -> None:
    if cache_stats is not None:
        cache_stats[name] = cache_stats.get(name, 0) + amount


def pdf_source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _academic_page_segments(
    text: str,
    *,
    current_sections: list[str] | None = None,
) -> tuple[list[tuple[list[str], str]], list[str]]:
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
    return segments, sections


def split_academic_page_text(
    text: str,
    *,
    current_sections: list[str] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[list[str], list[str]]:
    segments, sections = _academic_page_segments(
        text,
        current_sections=current_sections,
    )

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


def split_sentence_aligned_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    normalized = re.sub(r"\s+", " ", normalize_pdf_text(text)).strip()
    if not normalized:
        return []
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than chunk_size")

    protected = normalized
    for abbreviation in SENTENCE_ABBREVIATIONS:
        protected_abbreviation = abbreviation.replace(".", PROTECTED_PERIOD)
        protected = re.sub(
            re.escape(abbreviation),
            protected_abbreviation,
            protected,
            flags=re.IGNORECASE,
        )
    sentences: list[str] = []
    for sentence in SENTENCE_BOUNDARY_PATTERN.split(protected):
        restored = re.sub(
            r"\s+",
            " ",
            sentence.replace(PROTECTED_PERIOD, "."),
        ).strip()
        if not restored:
            continue
        if len(restored) > chunk_size:
            sentences.extend(
                split_page_text(
                    restored,
                    chunk_size=chunk_size,
                    overlap=overlap,
                )
            )
        else:
            sentences.append(restored)
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []

    def joined_length(items: list[str]) -> int:
        return len(" ".join(items))

    def overlap_tail(items: list[str]) -> list[str]:
        selected: list[str] = []
        for sentence in reversed(items):
            selected.insert(0, sentence)
            if joined_length(selected) >= overlap:
                break
        return selected

    for sentence in sentences:
        if current and joined_length([*current, sentence]) > chunk_size:
            chunks.append(" ".join(current))
            current = overlap_tail(current) if overlap else []
            while current and joined_length([*current, sentence]) > chunk_size:
                current.pop(0)
        current.append(sentence)

    if current:
        chunks.append(" ".join(current))
    return chunks


def is_table_caption(line: str) -> bool:
    candidate = re.sub(r"\s+", " ", line).strip()
    match = TABLE_CAPTION_PATTERN.match(candidate)
    if match is None:
        return False
    return TABLE_REFERENCE_VERB_PATTERN.match(match.group("rest")) is None


def extract_table_contexts(
    text: str,
    *,
    max_context_chars: int = 420,
    max_following_lines: int = 4,
) -> list[tuple[str, str]]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    contexts: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if not is_table_caption(line):
            continue
        context_lines = [line]
        for following in lines[index + 1 : index + 1 + max_following_lines]:
            if not following:
                continue
            if (
                is_table_caption(following)
                or detect_section_heading(following) is not None
                or following.lower().startswith(("figure ", "fig. ", "algorithm "))
            ):
                break
            candidate = "\n".join([*context_lines, following])
            if len(candidate) > max_context_chars:
                break
            context_lines.append(following)
        contexts.append((line, "\n".join(context_lines)))
    return contexts


def split_table_context_page_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    normalized = normalize_pdf_text(text)
    chunks = split_page_text(
        normalized,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    contexts = extract_table_contexts(normalized)
    if not contexts:
        return chunks

    contextualized: list[str] = []
    active_context: str | None = None
    for chunk in chunks:
        matches = [
            (chunk.rfind(anchor), context)
            for anchor, context in contexts
            if anchor in chunk
        ]
        if matches:
            _, active_context = max(matches, key=lambda item: item[0])
        if active_context is None:
            contextualized.append(chunk)
        else:
            contextualized.append(
                f"{TABLE_CONTEXT_PREFIX}\n{active_context}\n"
                f"{TABLE_BODY_PREFIX}\n{chunk}"
            )
    return contextualized


def split_academic_sentence_page_text(
    text: str,
    *,
    current_sections: list[str] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[list[str], list[str]]:
    segments, sections = _academic_page_segments(
        text,
        current_sections=current_sections,
    )
    chunks: list[str] = []
    for section_path, body in segments:
        if section_path and _section_name(section_path[-1]) in EXCLUDED_SECTION_TITLES:
            continue
        section_label = " > ".join(section_path) if section_path else "Front Matter"
        prefix = f"[Section: {section_label}]"
        body_size = max(120, chunk_size - len(prefix) - 1)
        body_overlap = min(overlap, body_size - 1)
        for body_chunk in split_sentence_aligned_text(
            body,
            chunk_size=body_size,
            overlap=body_overlap,
        ):
            chunks.append(f"{prefix}\n{body_chunk}")
    return chunks, sections


def _vector_cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_breakpoint_threshold(
    similarities: Sequence[float],
    *,
    percentile: int = SEMANTIC_BREAKPOINT_PERCENTILE,
) -> float | None:
    if not similarities:
        return None
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(similarities)
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def merge_semantic_units(
    units: Sequence[str],
    vectors: Sequence[Sequence[float]],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    percentile: int = SEMANTIC_BREAKPOINT_PERCENTILE,
    threshold_override: float | None = None,
) -> tuple[list[str], int]:
    if len(units) != len(vectors):
        raise ValueError("semantic unit and vector counts must match")
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than chunk_size")
    if not units:
        return [], 0

    similarities = [
        _vector_cosine_similarity(vectors[index - 1], vectors[index])
        for index in range(1, len(vectors))
    ]
    threshold = (
        threshold_override
        if threshold_override is not None
        else semantic_breakpoint_threshold(
            similarities,
            percentile=percentile,
        )
    )
    minimum_body_size = min(chunk_size, max(300, chunk_size // 3))
    chunks: list[str] = []
    current: list[str] = []
    semantic_break_count = 0

    def joined_length(items: Sequence[str]) -> int:
        return len(" ".join(items))

    def overlap_tail(items: Sequence[str]) -> list[str]:
        selected: list[str] = []
        for unit in reversed(items):
            selected.insert(0, unit)
            if joined_length(selected) >= overlap:
                break
        return selected

    for index, unit in enumerate(units):
        semantic_break = (
            bool(current)
            and threshold is not None
            and similarities[index - 1] <= threshold
            and joined_length(current) >= minimum_body_size
        )
        forced_break = bool(current) and joined_length([*current, unit]) > chunk_size
        if semantic_break or forced_break:
            chunks.append(" ".join(current))
            if semantic_break:
                semantic_break_count += 1
            current = overlap_tail(current) if overlap else []
            while current and joined_length([*current, unit]) > chunk_size:
                current.pop(0)
        current.append(unit)

    if current:
        chunks.append(" ".join(current))
    return chunks, semantic_break_count


def collect_semantic_segments(
    pages: Sequence[str],
) -> list[tuple[int, list[str], list[str]]]:
    segments: list[tuple[int, list[str], list[str]]] = []
    current_sections: list[str] = []
    for page_number, page_text in enumerate(pages, start=1):
        page_segments, current_sections = _academic_page_segments(
            page_text,
            current_sections=current_sections,
        )
        for section_path, body in page_segments:
            if (
                section_path
                and _section_name(section_path[-1]) in EXCLUDED_SECTION_TITLES
            ):
                continue
            units = split_sentence_aligned_text(
                body,
                chunk_size=SEMANTIC_UNIT_SIZE,
                overlap=0,
            )
            if units:
                segments.append((page_number, section_path, units))
    return segments


def build_semantic_pdf_chunks(
    pages: Sequence[str],
    *,
    semantic_embedder: SemanticEmbedder,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    layout_stats: dict[str, object] | None = None,
    threshold_scope: str = "paper",
) -> list[PaperChunk]:
    if threshold_scope not in {"paper", "segment"}:
        raise ValueError("threshold_scope must be paper or segment")
    segments = collect_semantic_segments(pages)
    index_texts: list[str] = []
    for _page_number, section_path, units in segments:
        section_label = " > ".join(section_path) if section_path else "Front Matter"
        prefix = f"[Section: {section_label}]"
        index_texts.extend(f"{prefix}\n{unit}" for unit in units)

    vectors = semantic_embedder(index_texts)
    if len(vectors) != len(index_texts):
        raise RuntimeError("semantic embedder returned an unexpected vector count")

    global_similarities: list[float] = []
    vector_offset = 0
    for _page_number, _section_path, units in segments:
        segment_vectors = vectors[vector_offset : vector_offset + len(units)]
        vector_offset += len(units)
        global_similarities.extend(
            _vector_cosine_similarity(
                segment_vectors[index - 1],
                segment_vectors[index],
            )
            for index in range(1, len(segment_vectors))
        )
    global_threshold = (
        semantic_breakpoint_threshold(
            global_similarities,
            percentile=SEMANTIC_BREAKPOINT_PERCENTILE,
        )
        if threshold_scope == "paper"
        else None
    )

    chunks: list[PaperChunk] = []
    chunk_index = 0
    vector_offset = 0
    semantic_break_count = 0
    for page_number, section_path, units in segments:
        segment_vectors = vectors[vector_offset : vector_offset + len(units)]
        vector_offset += len(units)
        body_chunks, break_count = merge_semantic_units(
            units,
            segment_vectors,
            chunk_size=chunk_size,
            overlap=overlap,
            threshold_override=global_threshold,
        )
        semantic_break_count += break_count
        section_label = " > ".join(section_path) if section_path else "Front Matter"
        prefix = f"[Section: {section_label}]"
        for body_chunk in body_chunks:
            chunks.append(
                PaperChunk(
                    page_number=page_number,
                    chunk_index=chunk_index,
                    content=f"{prefix}\n{body_chunk}",
                )
            )
            chunk_index += 1

    if layout_stats is not None:
        layout_stats.update(
            {
                "semantic_unit_count": len(index_texts),
                "semantic_break_count": semantic_break_count,
                "semantic_threshold_scope": threshold_scope,
                "semantic_threshold": (
                    round(global_threshold, 6)
                    if global_threshold is not None
                    else None
                ),
            }
        )
    return chunks


def parent_child_sizes(parent_size: int, parent_overlap: int) -> tuple[int, int]:
    child_size = max(240, parent_size // 2)
    child_overlap = min(max(0, parent_overlap // 2), child_size - 1)
    return child_size, child_overlap


def build_parent_child_chunks(
    pages: list[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[list[PaperChunk], list[PaperParentChunk]]:
    child_size, child_overlap = parent_child_sizes(chunk_size, overlap)
    children: list[PaperChunk] = []
    parents: list[PaperParentChunk] = []
    child_index = 0
    parent_index = 0

    for page_number, page_text in enumerate(pages, start=1):
        parent_contents = split_page_text(
            page_text,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        for parent_content in parent_contents:
            parents.append(
                PaperParentChunk(
                    page_number=page_number,
                    parent_index=parent_index,
                    content=parent_content,
                )
            )
            for child_content in split_sentence_aligned_text(
                parent_content,
                chunk_size=child_size,
                overlap=child_overlap,
            ):
                children.append(
                    PaperChunk(
                        page_number=page_number,
                        chunk_index=child_index,
                        content=child_content,
                        parent_index=parent_index,
                        role=CHUNK_ROLE_CHILD,
                    )
                )
                child_index += 1
            parent_index += 1
    return children, parents


def build_multi_granularity_chunks(
    pages: list[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    layout_stats: dict[str, object] | None = None,
) -> tuple[list[PaperChunk], list[PaperParentChunk]]:
    children, parents = build_parent_child_chunks(
        pages,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    chunks: list[PaperChunk] = []
    for parent in parents:
        chunks.append(
            PaperChunk(
                page_number=parent.page_number,
                chunk_index=len(chunks),
                content=parent.content,
                parent_index=parent.parent_index,
                role=CHUNK_ROLE_PARENT,
            )
        )
    for child in children:
        chunks.append(
            PaperChunk(
                page_number=child.page_number,
                chunk_index=len(chunks),
                content=child.content,
                parent_index=child.parent_index,
                role=CHUNK_ROLE_CHILD,
            )
        )
    if layout_stats is not None:
        layout_stats.update(
            {
                "parent_retrieval_chunk_count": len(parents),
                "child_retrieval_chunk_count": len(children),
            }
        )
    return chunks, parents


def extract_pdf_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf 依赖，请先执行 uv sync") from exc

    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def build_pdf_chunk_layout(
    pages: list[str],
    *,
    strategy: str = CHUNK_STRATEGY_LENGTH,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    semantic_embedder: SemanticEmbedder | None = None,
    layout_stats: dict[str, object] | None = None,
) -> tuple[list[PaperChunk], list[PaperParentChunk]]:
    if strategy not in SUPPORTED_CHUNK_STRATEGIES:
        raise ValueError(f"unsupported chunk strategy: {strategy}")

    if strategy == CHUNK_STRATEGY_PARENT_CHILD:
        return build_parent_child_chunks(
            pages,
            chunk_size=chunk_size,
            overlap=overlap,
        )
    if strategy == CHUNK_STRATEGY_MULTI_GRANULARITY:
        return build_multi_granularity_chunks(
            pages,
            chunk_size=chunk_size,
            overlap=overlap,
            layout_stats=layout_stats,
        )
    if strategy in {
        CHUNK_STRATEGY_SEMANTIC_LOCAL,
        CHUNK_STRATEGY_SEMANTIC,
    }:
        if semantic_embedder is None:
            raise ValueError("semantic_embedder is required for semantic chunking")
        return (
            build_semantic_pdf_chunks(
                pages,
                semantic_embedder=semantic_embedder,
                chunk_size=chunk_size,
                overlap=overlap,
                layout_stats=layout_stats,
                threshold_scope=(
                    "paper"
                    if strategy == CHUNK_STRATEGY_SEMANTIC
                    else "segment"
                ),
            ),
            [],
        )

    chunks: list[PaperChunk] = []
    chunk_index = 0
    current_sections: list[str] = []
    for page_number, page_text in enumerate(pages, start=1):
        if strategy in {
            CHUNK_STRATEGY_ACADEMIC,
            CHUNK_STRATEGY_ACADEMIC_NO_PREFIX,
        }:
            page_chunks, current_sections = split_academic_page_text(
                page_text,
                current_sections=current_sections,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        elif strategy == CHUNK_STRATEGY_PAPER_SENTENCE:
            page_chunks, current_sections = split_academic_sentence_page_text(
                page_text,
                current_sections=current_sections,
                chunk_size=chunk_size,
                overlap=overlap,
            )
        elif strategy == CHUNK_STRATEGY_TABLE_CONTEXT:
            page_chunks = split_table_context_page_text(
                page_text,
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
    return chunks, []


def build_pdf_chunks(
    pages: list[str],
    *,
    strategy: str = CHUNK_STRATEGY_LENGTH,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    semantic_embedder: SemanticEmbedder | None = None,
) -> list[PaperChunk]:
    chunks, _parents = build_pdf_chunk_layout(
        pages,
        strategy=strategy,
        chunk_size=chunk_size,
        overlap=overlap,
        semantic_embedder=semantic_embedder,
    )
    return chunks


def extract_pdf_chunks(
    path: Path,
    *,
    strategy: str = CHUNK_STRATEGY_LENGTH,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    semantic_embedder: SemanticEmbedder | None = None,
) -> tuple[int, list[PaperChunk]]:
    pages = extract_pdf_pages(path)
    chunks = build_pdf_chunks(
        pages,
        strategy=strategy,
        chunk_size=chunk_size,
        overlap=overlap,
        semantic_embedder=semantic_embedder,
    )
    return len(pages), chunks


def _parse_paper_pdf_legacy(
    paper_id: int,
    *,
    strategy: str = CHUNK_STRATEGY_LENGTH,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    cache_stats: dict[str, int] | None = None,
    semantic_embedder: SemanticEmbedder | None = None,
) -> dict[str, object]:
    paper = get_paper(paper_id)
    if paper is None:
        return {"chunks_reused": False}

    try:
        local_path = paper["local_path"]
        if not local_path:
            raise RuntimeError("当前论文没有可解析的本地 PDF")
        path = Path(local_path)
        if not path.is_file():
            raise RuntimeError("本地 PDF 文件不存在")

        source_hash = pdf_source_hash(path)
        document = get_document(paper_id)
        if (
            document is not None
            and document["parse_status"] == "parsed"
            and document["source_hash"] == source_hash
            and document["chunk_strategy"] == strategy
            and document["chunk_size"] == chunk_size
            and document["chunk_overlap"] == overlap
            and int(document["chunk_count"] or 0) > 0
            and (
                strategy
                not in {
                    CHUNK_STRATEGY_PARENT_CHILD,
                    CHUNK_STRATEGY_MULTI_GRANULARITY,
                }
                or count_paper_parent_chunks(paper_id) > 0
            )
        ):
            parent_chunk_count = count_paper_parent_chunks(paper_id)
            try:
                layout_stats = json.loads(document["layout_stats_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                layout_stats = {}
            _increment_cache_stat(
                cache_stats,
                "chunk_layout_hits",
                int(document["chunk_count"]),
            )
            return {
                "chunks_reused": True,
                "source_hash": source_hash,
                "page_count": int(document["page_count"]),
                "chunk_count": int(document["chunk_count"]),
                "parent_chunk_count": parent_chunk_count,
                "layout_stats": layout_stats,
            }

        update_parse_status(paper_id, PARSE_PARSING)
        pages = get_cached_pdf_pages(source_hash)
        if pages:
            _increment_cache_stat(cache_stats, "pdf_document_hits")
            _increment_cache_stat(cache_stats, "pdf_page_hits", len(pages))
        else:
            pages = extract_pdf_pages(path)
            if not pages:
                raise RuntimeError("PDF 没有可读取页面")
            replace_cached_pdf_pages(source_hash, pages)
            _increment_cache_stat(cache_stats, "pdf_document_misses")
            _increment_cache_stat(cache_stats, "pdf_page_misses", len(pages))

        layout_stats: dict[str, object] = {}
        chunks, parent_chunks = build_pdf_chunk_layout(
            pages,
            strategy=strategy,
            chunk_size=chunk_size,
            overlap=overlap,
            semantic_embedder=semantic_embedder,
            layout_stats=layout_stats,
        )
        if not chunks:
            raise RuntimeError("PDF 未提取到可用文本，文件可能是扫描版")
        replace_paper_chunks(
            paper_id,
            chunks,
            page_count=len(pages),
            parent_chunks=parent_chunks,
            source_hash=source_hash,
            chunk_strategy=strategy,
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            layout_stats=layout_stats,
            parser_backend="legacy_pypdf",
            parser_model="pypdf",
        )
        _increment_cache_stat(cache_stats, "chunk_layout_misses", len(chunks))
        return {
            "chunks_reused": False,
            "source_hash": source_hash,
            "page_count": len(pages),
            "chunk_count": len(chunks),
            "parent_chunk_count": len(parent_chunks),
            "layout_stats": layout_stats,
        }
    except Exception as exc:
        update_parse_status(paper_id, PARSE_FAILED, error=str(exc)[:500])
        return {"chunks_reused": False, "error": str(exc)[:500]}


def _parse_paper_pdf_with_mineru(
    paper_id: int,
    *,
    cache_stats: dict[str, int] | None = None,
) -> dict[str, object]:
    paper = get_paper(paper_id)
    if paper is None:
        return {"chunks_reused": False}

    try:
        local_path = paper["local_path"]
        if not local_path:
            raise RuntimeError("当前论文没有可解析的本地 PDF")
        path = Path(local_path)
        if not path.is_file():
            raise RuntimeError("本地 PDF 文件不存在")

        source_hash = pdf_source_hash(path)
        document = get_document(paper_id)
        if (
            document is not None
            and document["parse_status"] == "parsed"
            and document["source_hash"] == source_hash
            and document["chunk_strategy"] == MINERU_CHUNK_STRATEGY
            and document["parser_backend"] == MINERU_PARSER_BACKEND
            and document["parser_model"] == MINERU_MODEL_VERSION
            and int(document["chunk_count"] or 0) > 0
            and count_paper_parent_chunks(paper_id) > 0
        ):
            _increment_cache_stat(
                cache_stats,
                "mineru_chunk_hits",
                int(document["chunk_count"]),
            )
            try:
                layout_stats = json.loads(document["layout_stats_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                layout_stats = {}
            parent_chunk_count = count_paper_parent_chunks(paper_id)
            return {
                "chunks_reused": True,
                "source_hash": source_hash,
                "page_count": int(document["page_count"] or 0),
                "chunk_count": int(document["chunk_count"] or 0),
                "parent_chunk_count": parent_chunk_count,
                "layout_stats": layout_stats,
                "parser_backend": MINERU_PARSER_BACKEND,
                "parser_job_id": document["parser_job_id"],
            }

        cached_content = load_cached_content_list(source_hash, MINERU_MODEL_VERSION)
        batch_id = ""
        if cached_content is not None:
            content_list = cached_content
            if document is not None:
                batch_id = str(document["parser_job_id"] or "")
            _increment_cache_stat(cache_stats, "mineru_result_hits")
        else:
            _increment_cache_stat(cache_stats, "mineru_result_misses")
            existing_batch_id = None
            if (
                document is not None
                and document["source_hash"] == source_hash
                and document["parser_backend"] == MINERU_PARSER_BACKEND
                and document["parser_model"] == MINERU_MODEL_VERSION
                and document["parser_job_id"]
                and str(document["parse_stage"] or "")
                in {"pending", "running", "converting", "done"}
            ):
                existing_batch_id = str(document["parser_job_id"])

            update_remote_parse_state(
                paper_id,
                source_hash=source_hash,
                parser_backend=MINERU_PARSER_BACKEND,
                parser_model=MINERU_MODEL_VERSION,
                parser_job_id=existing_batch_id or "",
                parse_stage="submitting" if existing_batch_id is None else "running",
            )

            def record_progress(
                state: str,
                remote_batch_id: str | None,
                _extracted_pages: int | None,
                _total_pages: int | None,
            ) -> None:
                update_remote_parse_state(
                    paper_id,
                    source_hash=source_hash,
                    parser_backend=MINERU_PARSER_BACKEND,
                    parser_model=MINERU_MODEL_VERSION,
                    parser_job_id=remote_batch_id,
                    parse_stage=state,
                )

            result = parse_pdf_with_mineru(
                path,
                source_hash=source_hash,
                data_id=f"paper_{paper_id}_{source_hash[:16]}",
                existing_batch_id=existing_batch_id,
                on_progress=record_progress,
            )
            content_list = result.content_list
            batch_id = result.batch_id

        page_count, chunks, parent_chunks, layout_stats = build_structured_chunks(
            content_list
        )
        if not chunks:
            raise RuntimeError("MinerU没有提取到可用于检索的论文内容")
        layout_stats["mineru_result_cache_hit"] = cached_content is not None
        replace_paper_chunks(
            paper_id,
            chunks,
            page_count=page_count,
            parent_chunks=parent_chunks,
            source_hash=source_hash,
            chunk_strategy=MINERU_CHUNK_STRATEGY,
            layout_stats=layout_stats,
            parser_backend=MINERU_PARSER_BACKEND,
            parser_model=MINERU_MODEL_VERSION,
            parser_job_id=batch_id or None,
        )
        _increment_cache_stat(cache_stats, "mineru_chunk_misses", len(chunks))
        return {
            "chunks_reused": False,
            "source_hash": source_hash,
            "page_count": page_count,
            "chunk_count": len(chunks),
            "parent_chunk_count": len(parent_chunks),
            "layout_stats": layout_stats,
            "parser_backend": MINERU_PARSER_BACKEND,
            "parser_job_id": batch_id or None,
        }
    except Exception as exc:
        update_parse_status(paper_id, PARSE_FAILED, error=str(exc)[:500])
        return {"chunks_reused": False, "error": str(exc)[:500]}


def parse_paper_pdf(
    paper_id: int,
    *,
    strategy: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    cache_stats: dict[str, int] | None = None,
    semantic_embedder: SemanticEmbedder | None = None,
) -> dict[str, object]:
    if strategy is None:
        return _parse_paper_pdf_with_mineru(paper_id, cache_stats=cache_stats)
    return _parse_paper_pdf_legacy(
        paper_id,
        strategy=strategy,
        chunk_size=chunk_size,
        overlap=overlap,
        cache_stats=cache_stats,
        semantic_embedder=semantic_embedder,
    )

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


CONCLUSION_SECTION_ALIASES = (
    "conclusion",
    "conclusions",
    "concluding remarks",
    "summary and conclusions",
    "discussion and conclusion",
    "discussion and conclusions",
)
DISCUSSION_SECTION_ALIASES = ("discussion",)
CONCLUSION_INTENT_PATTERN = re.compile(
    r"(?<![a-z0-9])conclusions?(?![a-z0-9])|"
    r"(?<![a-z0-9])concluding\s+remarks?(?![a-z0-9])|"
    r"结论|总结(?:部分|章节)?",
    re.IGNORECASE,
)
SECTION_NUMBER_PREFIX = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|(?:[ivxlcdm]+)|(?:[a-z]))[.)]?\s+",
    re.IGNORECASE,
)


def section_leaf(section_path: object) -> str:
    path = str(section_path or "").strip()
    if not path:
        return ""
    return path.rsplit(" > ", 1)[-1].strip()


def _normalized_section_name(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", section_leaf(value)).casefold()
    normalized = normalized.replace("&", " and ")
    normalized = SECTION_NUMBER_PREFIX.sub("", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def resolve_section_alias(
    question: str,
    section_paths: Sequence[object],
) -> dict[str, Any] | None:
    """Resolve Conclusion intent against only real parsed section paths."""

    if not CONCLUSION_INTENT_PATTERN.search(str(question or "")):
        return None

    real_sections = []
    seen = set()
    for raw_path in section_paths:
        path = str(raw_path or "").strip()
        normalized = _normalized_section_name(path)
        if not path or not normalized or path in seen:
            continue
        seen.add(path)
        real_sections.append((path, section_leaf(path), normalized))

    def first_match(aliases: Sequence[str]) -> tuple[str, str] | None:
        for alias in aliases:
            normalized_alias = _normalized_section_name(alias)
            for path, title, normalized in real_sections:
                if normalized == normalized_alias:
                    return path, title
        return None

    direct = first_match(CONCLUSION_SECTION_ALIASES)
    if direct is not None:
        path, title = direct
        return {
            "intent": "conclusion",
            "requested_section": "Conclusion",
            "matched_section_title": title,
            "matched_section_path": path,
            "match_type": "direct",
            "response_note": (
                f"论文存在真实的 {title} 章节，应优先依据该章节回答。"
            ),
        }

    discussion = first_match(DISCUSSION_SECTION_ALIASES)
    if discussion is not None:
        path, title = discussion
        return {
            "intent": "conclusion",
            "requested_section": "Conclusion",
            "matched_section_title": title,
            "matched_section_path": path,
            "match_type": "discussion_fallback",
            "response_note": (
                "论文没有单独的 Conclusion 章节；结论性内容主要位于真实的 "
                "Discussion 章节。回答时必须明确说明这一章节差异。"
            ),
        }

    return {
        "intent": "conclusion",
        "requested_section": "Conclusion",
        "matched_section_title": "",
        "matched_section_path": "",
        "match_type": "not_found",
        "response_note": "",
    }


def apply_section_alignment(
    evidence: Sequence[Mapping[str, Any]],
    resolution: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach a resolved alias only to evidence from the real matched section."""

    items = [dict(item) for item in evidence]
    if not resolution or resolution.get("match_type") == "not_found":
        return items
    matched_path = str(resolution.get("matched_section_path") or "").strip()
    if not matched_path:
        return items
    for item in items:
        item_path = str(
            item.get("section_path") or item.get("section_title") or ""
        ).strip()
        if item_path == matched_path:
            item["section_alignment"] = dict(resolution)
    return items

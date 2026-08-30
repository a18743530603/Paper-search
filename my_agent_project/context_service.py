import math
import re
import unicodedata
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


DEFAULT_CONTEXT_TOKEN_BUDGET = 6000
DEFAULT_MIN_CHUNKS_PER_PAPER = 1
DEFAULT_OVERLAP_DUPLICATE_THRESHOLD = 0.85
MIN_OVERLAP_SHINGLES = 12
OVERLAP_SHINGLE_SIZE = 5
CONTEXT_QUALITY_GATE_VERSION = "context-quality-gate-v1"

_CJK_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
)
_BILINGUAL_TOKEN_PATTERN = re.compile(
    r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContextConfig:
    max_estimated_tokens: int = DEFAULT_CONTEXT_TOKEN_BUDGET
    min_chunks_per_paper: int = DEFAULT_MIN_CHUNKS_PER_PAPER

    def __post_init__(self) -> None:
        if self.max_estimated_tokens < 1:
            raise ValueError("max_estimated_tokens必须为正整数")
        if self.min_chunks_per_paper < 0:
            raise ValueError("min_chunks_per_paper不能小于0")


def estimate_text_tokens(text: str) -> int:
    normalized = str(text or "")
    if not normalized:
        return 0
    cjk_count = len(_CJK_PATTERN.findall(normalized))
    non_cjk_count = sum(
        1
        for character in normalized
        if not character.isspace() and not _CJK_PATTERN.fullmatch(character)
    )
    return cjk_count + math.ceil(non_cjk_count / 4)


def evidence_context_text(item: Mapping[str, Any]) -> str:
    metadata = [
        f"论文：{item.get('paper_title', '')}",
        f"物理页：{item.get('page_number', '')}",
    ]
    section_title = str(item.get("section_title") or "").strip()
    if section_title:
        metadata.insert(1, f"章节：{section_title}")
    alignment = item.get("section_alignment")
    alignment_note = (
        str(alignment.get("response_note") or "").strip()
        if isinstance(alignment, Mapping)
        else ""
    )
    suffix = f"\n章节对应：{alignment_note}" if alignment_note else ""
    return f"[{'；'.join(metadata)}]{suffix}\n{item.get('content', '')}"


def estimate_evidence_tokens(item: Mapping[str, Any]) -> int:
    """Estimate the exact evidence block serialized into research context."""

    return estimate_text_tokens(evidence_context_text(item))


def _paper_key(item: Mapping[str, Any]) -> str:
    paper_id = item.get("paper_id")
    if paper_id is not None:
        return f"id:{paper_id}"
    title = " ".join(str(item.get("paper_title") or "未知论文").split())
    return f"title:{title.casefold()}"


def _positive_integer(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def evidence_validation_errors(item: Mapping[str, Any]) -> list[str]:
    errors = []
    if _positive_integer(item.get("paper_id")) is None:
        errors.append("invalid_paper_id")
    if _positive_integer(item.get("chunk_id")) is None:
        errors.append("invalid_chunk_id")
    if not str(item.get("paper_title") or "").strip():
        errors.append("missing_paper_title")
    if _positive_integer(item.get("page_number")) is None:
        errors.append("invalid_page_number")
    if not str(item.get("content") or "").strip():
        errors.append("empty_content")
    return errors


def normalize_evidence_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join(normalized.split())


def _text_shingles(text: str) -> set[tuple[str, ...]]:
    tokens = _BILINGUAL_TOKEN_PATTERN.findall(normalize_evidence_text(text))
    if not tokens:
        return set()
    if len(tokens) < OVERLAP_SHINGLE_SIZE:
        return {tuple(tokens)}
    return {
        tuple(tokens[index : index + OVERLAP_SHINGLE_SIZE])
        for index in range(len(tokens) - OVERLAP_SHINGLE_SIZE + 1)
    }


def _page_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    try:
        return abs(int(left.get("page_number") or 0) - int(right.get("page_number") or 0))
    except (TypeError, ValueError):
        return 999999


def evidence_overlap_ratio(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> float:
    left_shingles = _text_shingles(str(left.get("content") or ""))
    right_shingles = _text_shingles(str(right.get("content") or ""))
    smaller_count = min(len(left_shingles), len(right_shingles))
    if smaller_count < MIN_OVERLAP_SHINGLES:
        return 0.0
    return len(left_shingles & right_shingles) / smaller_count


def _duplicate_reference(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": item.get("paper_id"),
        "paper_title": item.get("paper_title", "未知论文"),
        "page_number": item.get("page_number"),
        "chunk_id": item.get("chunk_id"),
    }


def deduplicate_paper_evidence(
    candidates: Sequence[Mapping[str, Any]],
    *,
    overlap_threshold: float = DEFAULT_OVERLAP_DUPLICATE_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not 0.0 <= overlap_threshold <= 1.0:
        raise ValueError("overlap_threshold必须在0到1之间")
    ordered = [dict(item) for item in candidates]
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    normalized_kept: list[str] = []

    for candidate in ordered:
        normalized = normalize_evidence_text(str(candidate.get("content") or ""))
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(normalized_kept)
                if normalized
                and normalized == existing
                and _paper_key(candidate) == _paper_key(kept[index])
            ),
            None,
        )
        if duplicate_index is not None:
            audit = _evidence_audit_item(candidate, reason="exact_duplicate")
            audit["overlap_ratio"] = 1.0
            audit["duplicate_of"] = _duplicate_reference(kept[duplicate_index])
            dropped.append(audit)
            continue

        overlap_match = None
        for index, existing in enumerate(kept):
            if _paper_key(candidate) != _paper_key(existing):
                continue
            if _page_distance(candidate, existing) > 1:
                continue
            overlap_ratio = evidence_overlap_ratio(candidate, existing)
            if overlap_ratio >= overlap_threshold:
                overlap_match = (index, overlap_ratio)
                break
        if overlap_match is not None:
            duplicate_index, overlap_ratio = overlap_match
            audit = _evidence_audit_item(candidate, reason="overlap_duplicate")
            audit["overlap_ratio"] = round(overlap_ratio, 4)
            audit["duplicate_of"] = _duplicate_reference(kept[duplicate_index])
            dropped.append(audit)
            continue

        kept.append(candidate)
        normalized_kept.append(normalized)
    return kept, dropped


def _evidence_audit_item(
    item: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "paper_id": item.get("paper_id"),
        "paper_title": item.get("paper_title", "未知论文"),
        "page_number": item.get("page_number"),
        "chunk_id": item.get("chunk_id"),
        "estimated_tokens": estimate_evidence_tokens(item),
        "reason": reason,
    }


def _round_robin_selected_evidence(
    grouped: Mapping[str, Sequence[dict[str, Any]]],
    selected_indexes: Mapping[str, set[int]],
) -> list[dict[str, Any]]:
    selected_by_paper = {
        paper_key: [
            candidate
            for index, candidate in enumerate(candidates)
            if index in selected_indexes[paper_key]
        ]
        for paper_key, candidates in grouped.items()
    }
    max_selected = max(
        (len(candidates) for candidates in selected_by_paper.values()),
        default=0,
    )
    selected: list[dict[str, Any]] = []
    for rank in range(max_selected):
        for candidates in selected_by_paper.values():
            if rank < len(candidates):
                selected.append(candidates[rank])
    return selected


def pack_research_context(
    evidence: Sequence[Mapping[str, Any]],
    *,
    config: ContextConfig | None = None,
) -> dict[str, Any]:
    active_config = config or ContextConfig()
    raw_evidence = [dict(item) for item in evidence]
    valid_evidence: list[dict[str, Any]] = []
    invalid_dropped: list[dict[str, Any]] = []
    for item in raw_evidence:
        validation_errors = evidence_validation_errors(item)
        if validation_errors:
            audit = _evidence_audit_item(item, reason="invalid_evidence")
            audit["validation_errors"] = validation_errors
            invalid_dropped.append(audit)
            continue
        valid_evidence.append(item)
    raw_grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in valid_evidence:
        raw_grouped.setdefault(_paper_key(item), []).append(item)
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    duplicate_dropped: list[dict[str, Any]] = []
    for paper_key, candidates in raw_grouped.items():
        deduplicated, dropped = deduplicate_paper_evidence(candidates)
        grouped[paper_key] = deduplicated
        duplicate_dropped.extend(dropped)

    dropped: list[dict[str, Any]] = [*invalid_dropped, *duplicate_dropped]
    selected_tokens = 0
    selected_indexes = {key: set() for key in grouped}
    selected_tokens_per_paper = {key: 0 for key in grouped}
    coverage_status = {key: "deferred" for key in grouped}
    coverage_selected = 0
    fill_selected = 0

    uncovered_papers = set(grouped)
    for paper_key, candidates in grouped.items():
        if not candidates:
            uncovered_papers.discard(paper_key)
            coverage_status[paper_key] = "no_candidates"
            continue
        remaining_budget = active_config.max_estimated_tokens - selected_tokens
        remaining_uncovered = max(1, len(uncovered_papers))
        coverage_allowance = remaining_budget / remaining_uncovered
        for index, item in enumerate(candidates):
            item_tokens = estimate_evidence_tokens(item)
            if item_tokens > coverage_allowance:
                continue
            selected_indexes[paper_key].add(index)
            selected_tokens += item_tokens
            selected_tokens_per_paper[paper_key] += item_tokens
            coverage_status[paper_key] = "selected"
            coverage_selected += 1
            uncovered_papers.discard(paper_key)
            break

    max_candidate_count = max(
        (len(candidates) for candidates in grouped.values()),
        default=0,
    )
    for rank in range(max_candidate_count):
        for paper_key, candidates in grouped.items():
            if rank >= len(candidates) or rank in selected_indexes[paper_key]:
                continue
            item = candidates[rank]
            item_tokens = estimate_evidence_tokens(item)
            if selected_tokens + item_tokens > active_config.max_estimated_tokens:
                dropped.append(_evidence_audit_item(item, reason="token_budget"))
                continue
            selected_indexes[paper_key].add(rank)
            selected_tokens += item_tokens
            selected_tokens_per_paper[paper_key] += item_tokens
            fill_selected += 1

    selected = _round_robin_selected_evidence(grouped, selected_indexes)
    selected_per_paper = {
        paper_key: len(indexes)
        for paper_key, indexes in selected_indexes.items()
    }

    raw_tokens = sum(
        estimate_evidence_tokens(item)
        for item in raw_evidence
    )
    raw_characters = sum(len(evidence_context_text(item)) for item in raw_evidence)
    selected_characters = sum(
        len(evidence_context_text(item)) for item in selected
    )
    covered_papers = sum(1 for count in selected_per_paper.values() if count > 0)
    papers_below_minimum = [
        paper_key
        for paper_key, count in selected_per_paper.items()
        if count < active_config.min_chunks_per_paper
    ]
    per_paper = []
    for paper_key, candidates in grouped.items():
        first = candidates[0]
        per_paper.append(
            {
                "paper_key": paper_key,
                "paper_id": first.get("paper_id"),
                "paper_title": first.get("paper_title", "未知论文"),
                "raw_chunks": len(raw_grouped[paper_key]),
                "post_dedup_chunks": len(candidates),
                "selected_chunks": selected_per_paper[paper_key],
                "selected_estimated_tokens": selected_tokens_per_paper[paper_key],
                "coverage_status": coverage_status[paper_key],
            }
        )

    dropped_token_budget = sum(
        1 for item in dropped if item["reason"] == "token_budget"
    )
    dropped_duplicate = len(duplicate_dropped)
    stats = {
        "strategy": "dynamic_v1",
        "allocation_strategy": "dynamic_v1",
        "evidence_budget_tokens": active_config.max_estimated_tokens,
        "selected_tokens": selected_tokens,
        "coverage_selected": coverage_selected,
        "fill_selected": fill_selected,
        "papers_with_candidates": len(grouped),
        "papers_with_evidence": covered_papers,
        "papers_without_evidence": len(grouped) - covered_papers,
        "dropped_token_budget": dropped_token_budget,
        "dropped_duplicate": dropped_duplicate,
        "config": asdict(active_config),
        "raw_evidence_count": len(raw_evidence),
        "valid_evidence_count": len(valid_evidence),
        "invalid_evidence_count": len(invalid_dropped),
        "post_dedup_evidence_count": sum(len(items) for items in grouped.values()),
        "exact_duplicate_count": sum(
            1 for item in duplicate_dropped if item["reason"] == "exact_duplicate"
        ),
        "overlap_duplicate_count": sum(
            1 for item in duplicate_dropped if item["reason"] == "overlap_duplicate"
        ),
        "selected_evidence_count": len(selected),
        "dropped_evidence_count": len(dropped),
        "raw_character_count": raw_characters,
        "selected_character_count": selected_characters,
        "raw_estimated_tokens": raw_tokens,
        "selected_estimated_tokens": selected_tokens,
        "estimated_token_reduction_percent": (
            round((raw_tokens - selected_tokens) / raw_tokens * 100, 2)
            if raw_tokens
            else 0.0
        ),
        "paper_count": len(grouped),
        "covered_paper_count": covered_papers,
        "paper_coverage_percent": (
            round(covered_papers / len(grouped) * 100, 2)
            if grouped
            else 0.0
        ),
        "minimum_quota_satisfied": not papers_below_minimum,
        "papers_below_minimum": papers_below_minimum,
        "per_paper": per_paper,
        "dropped": dropped,
    }
    count_consistent = stats["selected_evidence_count"] == len(selected)
    budget_respected = selected_tokens <= active_config.max_estimated_tokens
    context_available = bool(selected)
    warnings = []
    if invalid_dropped:
        warnings.append("部分证据缺少必需字段，已在模型调用前剔除")
    if not stats["minimum_quota_satisfied"]:
        warnings.append("当前Token预算无法满足每篇论文的最低证据配额")
    if not context_available:
        warnings.append("没有通过质量门禁且能放入预算的证据")

    if not context_available or not budget_respected or not count_consistent:
        gate_status = "failed"
    elif warnings:
        gate_status = "warning"
    else:
        gate_status = "passed"
    quality_gate = {
        "version": CONTEXT_QUALITY_GATE_VERSION,
        "status": gate_status,
        "model_call_allowed": (
            context_available and budget_respected and count_consistent
        ),
        "checks": {
            "context_available": context_available,
            "required_fields_valid": not invalid_dropped,
            "token_budget_respected": budget_respected,
            "selected_count_consistent": count_consistent,
            "minimum_quota_satisfied": stats["minimum_quota_satisfied"],
        },
        "input_evidence_count": len(raw_evidence),
        "valid_evidence_count": len(valid_evidence),
        "accepted_evidence_count": len(selected),
        "rejected_evidence_count": len(invalid_dropped),
        "warnings": warnings,
    }
    return {
        "evidence": selected,
        "stats": stats,
        "quality_gate": quality_gate,
    }

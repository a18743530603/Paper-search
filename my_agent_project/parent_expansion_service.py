from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .context_service import DEFAULT_CONTEXT_TOKEN_BUDGET, estimate_evidence_tokens
from .db import get_paper_parent_chunks


ParentLookup = Callable[[int, Sequence[int]], Mapping[int, Mapping[str, Any]]]


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _seed_rank(seed: Mapping[str, Any], position: int) -> int:
    return _positive_int(seed.get("mmr_rank")) or position


def _seed_fallback(seed: Mapping[str, Any], position: int) -> dict[str, Any]:
    fallback = dict(seed)
    fallback["parent_chunk_id"] = None
    fallback["seed_chunk_ids"] = [int(seed["chunk_id"])]
    fallback["seed_mmr_rank"] = _seed_rank(seed, position)
    return fallback


def expand_seed_chunks_to_parents(
    seed_chunks: Sequence[Mapping[str, Any]],
    *,
    paper_id: int,
    paper_title: str = "",
    max_estimated_tokens: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
    parent_lookup: ParentLookup | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Expand only selected small chunks to stored parents, with child fallback."""

    if max_estimated_tokens < 1:
        raise ValueError("max_estimated_tokens must be positive")
    seeds = [dict(seed) for seed in seed_chunks]
    diagnostics: dict[str, Any] = {
        "seed_chunk_ids": [int(seed["chunk_id"]) for seed in seeds],
        "expanded_parent_ids": [],
        "expanded_evidence_count": 0,
        "parent_dedup_count": 0,
        "parent_fallback_chunk_ids": [],
        "parent_expansion_error": "",
        "parent_expansion_details": [],
    }
    if not seeds:
        diagnostics["parent_expansion_status"] = "fallback_no_seeds"
        return [], diagnostics

    requested_parent_ids = [
        parent_id
        for seed in seeds
        if (parent_id := _positive_int(seed.get("parent_chunk_id"))) is not None
    ]
    lookup = parent_lookup or get_paper_parent_chunks
    try:
        parent_rows = dict(lookup(paper_id, requested_parent_ids))
    except Exception as exc:
        diagnostics["parent_expansion_status"] = "fallback_lookup_error"
        diagnostics["parent_expansion_error"] = f"{type(exc).__name__}: {exc}"[:500]
        fallback = []
        for position, seed in enumerate(seeds, start=1):
            fallback.append(_seed_fallback(seed, position))
            seed_id = int(seed["chunk_id"])
            diagnostics["parent_fallback_chunk_ids"].append(seed_id)
            diagnostics["parent_expansion_details"].append(
                {
                    "seed_chunk_id": seed_id,
                    "parent_id": _positive_int(seed.get("parent_chunk_id")),
                    "action": "fallback_lookup_error",
                }
            )
        diagnostics["expanded_evidence_count"] = len(fallback)
        return fallback, diagnostics

    expanded: list[dict[str, Any]] = []
    expanded_parent_positions: dict[int, int] = {}
    expanded_count = 0
    for position, seed in enumerate(seeds, start=1):
        seed_id = int(seed["chunk_id"])
        parent_id = _positive_int(seed.get("parent_chunk_id"))
        action = "expanded"
        parent = parent_rows.get(parent_id) if parent_id is not None else None

        if parent_id is None:
            action = "fallback_no_parent"
        elif parent is None:
            action = "fallback_missing_parent"
        elif _positive_int(seed.get("paper_id")) != paper_id:
            action = "fallback_cross_paper"
        elif _positive_int(parent.get("paper_id")) != paper_id:
            action = "fallback_cross_paper"
        elif not isinstance(parent.get("content"), str) or not str(
            parent.get("content")
        ).strip():
            action = "fallback_invalid_parent"
        elif _positive_int(parent.get("page_number")) is None:
            action = "fallback_invalid_parent"
        elif estimate_evidence_tokens(
            {
                "paper_title": paper_title,
                "page_number": parent["page_number"],
                "content": parent["content"],
            }
        ) > max_estimated_tokens:
            action = "fallback_oversized"
        elif parent_id in expanded_parent_positions:
            action = "deduplicated_parent"

        if action == "deduplicated_parent":
            existing = expanded[expanded_parent_positions[parent_id]]
            existing["seed_chunk_ids"].append(seed_id)
            diagnostics["parent_dedup_count"] += 1
        elif action != "expanded":
            expanded.append(_seed_fallback(seed, position))
            diagnostics["parent_fallback_chunk_ids"].append(seed_id)
        else:
            parent_evidence = dict(seed)
            parent_evidence.update(
                {
                    "parent_chunk_id": parent_id,
                    "page_number": int(parent["page_number"]),
                    "content": parent["content"],
                    "bbox": None,
                    "seed_chunk_ids": [seed_id],
                    "seed_mmr_rank": _seed_rank(seed, position),
                }
            )
            expanded_parent_positions[parent_id] = len(expanded)
            expanded.append(parent_evidence)
            diagnostics["expanded_parent_ids"].append(parent_id)
            expanded_count += 1

        diagnostics["parent_expansion_details"].append(
            {
                "seed_chunk_id": seed_id,
                "parent_id": parent_id,
                "action": action,
            }
        )

    diagnostics["expanded_evidence_count"] = len(expanded)
    if expanded_count and diagnostics["parent_fallback_chunk_ids"]:
        diagnostics["parent_expansion_status"] = "applied_with_fallbacks"
    elif expanded_count:
        diagnostics["parent_expansion_status"] = "applied"
    else:
        diagnostics["parent_expansion_status"] = "fallback_all_seeds"
    return expanded, diagnostics

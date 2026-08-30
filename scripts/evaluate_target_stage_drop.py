"""Trace frozen target chunks through the production retrieval pipeline.

The experiment wraps production stage functions only for the lifetime of this
process.  It captures IDs, ranks, and scores without changing retrieval logic,
calling DeepSeek, or writing Business Database/cache rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.evaluate_evidence_budget_ablation import _table_counts
from scripts.evaluate_retrieval_fusion_ab import (
    DEFAULT_GROUND_TRUTH,
    ROOT,
    _load_query_vector,
)
from scripts.evaluate_retrieval_query_language_ab import (
    DEFAULT_ENGLISH_QUERIES,
    load_english_queries,
)


DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "target_stage_drop_acceptance_v1.json"
)
STAGES = (
    "hybrid_raw",
    "candidate_pool",
    "reranker",
    "mmr",
    "parent_expansion",
    "allocation",
)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return round(float(value), 6) if value is not None else None
    except (TypeError, ValueError):
        return None


def _snapshot_candidate(
    item: Mapping[str, Any],
    *,
    fallback_rank: int,
) -> dict[str, Any]:
    """Keep diagnostic metadata only; never persist text or embeddings."""

    return {
        "chunk_id": int(item["chunk_id"]),
        "paper_id": _optional_int(item.get("paper_id")),
        "parent_chunk_id": _optional_int(item.get("parent_chunk_id")),
        "page_number": _optional_int(item.get("page_number")),
        "chunk_index": _optional_int(item.get("chunk_index")),
        "hybrid_rank": _optional_int(item.get("hybrid_rank")) or fallback_rank,
        "hybrid_score": _optional_float(
            item.get("hybrid_score", item.get("score"))
        ),
        "semantic_score": _optional_float(item.get("semantic_score")),
        "tfidf_score": _optional_float(
            item.get("tfidf_score", item.get("keyword_score"))
        ),
        "rerank_rank": _optional_int(item.get("rerank_rank")),
        "rerank_score": _optional_float(item.get("rerank_score")),
        "mmr_rank": _optional_int(item.get("mmr_rank")),
        "mmr_score": _optional_float(item.get("mmr_score")),
        "mmr_relevance": _optional_float(item.get("mmr_relevance")),
        "mmr_max_similarity": _optional_float(item.get("mmr_max_similarity")),
        "seed_mmr_rank": _optional_int(item.get("seed_mmr_rank")),
        "seed_chunk_ids": [
            int(value) for value in (item.get("seed_chunk_ids") or [])
        ],
    }


def _snapshot_stage(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        _snapshot_candidate(item, fallback_rank=rank)
        for rank, item in enumerate(items, start=1)
    ]


def _candidate_by_id(
    items: Sequence[Mapping[str, Any]],
) -> dict[int, Mapping[str, Any]]:
    return {int(item["chunk_id"]): item for item in items}


def _exact_target_hits(
    target_ids: set[int],
    items: Sequence[Mapping[str, Any]],
) -> set[int]:
    return target_ids & set(_candidate_by_id(items))


def _expanded_target_coverage(
    target_ids: set[int],
    target_parent_ids: Mapping[int, int | None],
    items: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    coverage: dict[int, dict[str, Any]] = {}
    for target_id in target_ids:
        target_parent_id = target_parent_ids.get(target_id)
        for item in items:
            item_chunk_id = int(item["chunk_id"])
            item_parent_id = _optional_int(item.get("parent_chunk_id"))
            seed_ids = {int(value) for value in (item.get("seed_chunk_ids") or [])}
            if target_parent_id is not None and item_parent_id == target_parent_id:
                coverage[target_id] = {
                    "mode": "parent_covers_child",
                    "evidence_chunk_id": item_chunk_id,
                    "parent_id": item_parent_id,
                }
                break
            if item_parent_id is None and (
                item_chunk_id == target_id or target_id in seed_ids
            ):
                coverage[target_id] = {
                    "mode": "child_exact",
                    "evidence_chunk_id": item_chunk_id,
                    "parent_id": None,
                }
                break
    return coverage


def _stage_entry(
    items: Sequence[Mapping[str, Any]],
    target_hits: set[int],
) -> dict[str, Any]:
    return {
        "candidate_count": len(items),
        "target_hit_ids": sorted(target_hits),
        "candidates": list(items),
    }


def _loss_context(
    item: Mapping[str, Any] | None,
    *,
    paper_id: int,
) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "paper_id": paper_id,
        "hybrid_rank": item.get("hybrid_rank"),
        "hybrid_score": item.get("hybrid_score"),
        "semantic_score": item.get("semantic_score"),
        "tfidf_score": item.get("tfidf_score"),
        "rerank_rank": item.get("rerank_rank"),
        "rerank_score": item.get("rerank_score"),
        "mmr_rank": item.get("mmr_rank"),
        "mmr_score": item.get("mmr_score"),
    }


def _target_status(
    *,
    target: Mapping[str, Any],
    paper_id: int,
    stage_items: Mapping[str, Sequence[Mapping[str, Any]]],
    mmr_evaluations: Sequence[Mapping[str, Any]],
    parent_coverage: Mapping[int, Mapping[str, Any]],
    allocation_coverage: Mapping[int, Mapping[str, Any]],
    parent_status: str,
    final_top_k: int,
    guaranteed_chunk_ids: set[int],
) -> dict[str, Any]:
    target_id = int(target["chunk_id"])
    stage_maps = {
        stage: _candidate_by_id(stage_items[stage])
        for stage in ("hybrid_raw", "candidate_pool", "reranker", "mmr")
    }
    hybrid = stage_maps["hybrid_raw"].get(target_id)
    candidate_pool = stage_maps["candidate_pool"].get(target_id)
    reranker = stage_maps["reranker"].get(target_id)
    mmr = stage_maps["mmr"].get(target_id)
    mmr_evaluation = _candidate_by_id(mmr_evaluations).get(target_id)
    parent_hit = parent_coverage.get(target_id)
    final_hit = allocation_coverage.get(target_id)

    if hybrid is None:
        first_loss_stage = "hybrid_not_recalled"
        loss_reason = "non_positive_or_missing_hybrid_candidate"
        lost_from = None
    elif candidate_pool is None:
        first_loss_stage = "candidate_pool_truncation"
        loss_reason = "hybrid_rank_below_candidate_pool_cutoff"
        lost_from = hybrid
    elif reranker is None:
        first_loss_stage = "reranker_output"
        loss_reason = "candidate_missing_from_reranker_output"
        lost_from = candidate_pool
    elif mmr is None:
        first_loss_stage = "mmr_selection"
        rerank_position = int(reranker.get("rerank_rank") or 10**9)
        if rerank_position <= 2:
            loss_reason = "reranker_top2_safeguard_violation"
        elif rerank_position <= final_top_k:
            loss_reason = "mmr_diversity_displaced_reranker_top_k_target"
        else:
            loss_reason = "low_reranker_rank_not_rescued_by_mmr"
        lost_from = reranker
    elif parent_hit is None:
        first_loss_stage = "parent_expansion"
        loss_reason = "selected_child_not_represented_after_parent_expansion"
        lost_from = mmr
    elif final_hit is None:
        first_loss_stage = "allocation"
        loss_reason = "expanded_evidence_not_selected_by_allocation"
        lost_from = next(
            (
                item
                for item in stage_items["parent_expansion"]
                if int(item["chunk_id"])
                == int(parent_hit.get("evidence_chunk_id") or -1)
            ),
            None,
        )
    else:
        first_loss_stage = None
        loss_reason = None
        lost_from = None

    if hybrid is not None:
        first_appearance = "hybrid_raw"
    elif parent_hit is not None:
        first_appearance = "parent_expansion"
    elif final_hit is not None:
        first_appearance = "allocation"
    else:
        first_appearance = None

    rerank_rank = _optional_int(reranker.get("rerank_rank")) if reranker else None
    mmr_rank = (
        _optional_int(mmr_evaluation.get("mmr_rank"))
        if mmr_evaluation
        else None
    )
    return {
        "chunk_id": target_id,
        "paper_id": paper_id,
        "page_number": target.get("page_number"),
        "chunk_index": target.get("chunk_index"),
        "section_title": target.get("section_title"),
        "content_sha256": target.get("content_sha256"),
        "parent_chunk_id": target.get("parent_chunk_id"),
        "present_by_stage": {
            "hybrid_raw": hybrid is not None,
            "candidate_pool": candidate_pool is not None,
            "reranker": reranker is not None,
            "mmr": mmr is not None,
            "parent_expansion": parent_hit is not None,
            "allocation": final_hit is not None,
        },
        "first_appearance_stage": first_appearance,
        "first_loss_stage": first_loss_stage,
        "loss_reason": loss_reason,
        "lost_from": _loss_context(lost_from, paper_id=paper_id),
        "hybrid_rank": hybrid.get("hybrid_rank") if hybrid else None,
        "hybrid_score": hybrid.get("hybrid_score") if hybrid else None,
        "semantic_score": hybrid.get("semantic_score") if hybrid else None,
        "tfidf_score": hybrid.get("tfidf_score") if hybrid else None,
        "rerank_rank": rerank_rank,
        "rerank_score": reranker.get("rerank_score") if reranker else None,
        "rerank_rank_change_from_hybrid": (
            rerank_rank - int(hybrid["hybrid_rank"])
            if rerank_rank is not None and hybrid is not None
            else None
        ),
        "mmr_rank": mmr_rank,
        "mmr_selected": mmr is not None,
        "mmr_score": (
            mmr_evaluation.get("mmr_score") if mmr_evaluation else None
        ),
        "mmr_relevance": (
            mmr_evaluation.get("mmr_relevance") if mmr_evaluation else None
        ),
        "mmr_max_similarity": (
            mmr_evaluation.get("mmr_max_similarity")
            if mmr_evaluation
            else None
        ),
        "final_covered": final_hit is not None,
        "final_coverage_mode": final_hit.get("mode") if final_hit else None,
        "final_parent_id": final_hit.get("parent_id") if final_hit else None,
        "parent_expansion_status": parent_status,
        "guaranteed_by_reranker_top2": target_id in guaranteed_chunk_ids,
        "reranker_final_k_counterfactual_hit": (
            rerank_rank is not None and rerank_rank <= final_top_k
        ),
    }


def _distribution(values: Sequence[int]) -> dict[str, Any]:
    return {
        "min": min(values, default=0),
        "max": max(values, default=0),
        "mean": round(statistics.fmean(values), 6) if values else 0.0,
        "median": round(statistics.median(values), 6) if values else 0.0,
        "values": list(values),
    }


def _summarize(
    case_rows: Sequence[Mapping[str, Any]],
    *,
    target_count: int,
) -> dict[str, Any]:
    stage_hit_sets: dict[str, list[set[int]]] = {
        stage: [
            set(int(value) for value in row["stages"][stage]["target_hit_ids"])
            for row in case_rows
        ]
        for stage in STAGES
    }
    stage_metrics: dict[str, Any] = {}
    previous_stage: str | None = None
    for stage in STAGES:
        per_case_hits = stage_hit_sets[stage]
        hit_count = sum(len(values) for values in per_case_hits)
        question_hits = sum(bool(values) for values in per_case_hits)
        retained_count = None
        retention_rate = None
        previous_hit_count = None
        if previous_stage is not None:
            previous_sets = stage_hit_sets[previous_stage]
            previous_hit_count = sum(len(values) for values in previous_sets)
            retained_count = sum(
                len(previous & current)
                for previous, current in zip(previous_sets, per_case_hits)
            )
            retention_rate = (
                round(retained_count / previous_hit_count, 6)
                if previous_hit_count
                else None
            )
        candidate_counts = [
            int(row["stages"][stage]["candidate_count"]) for row in case_rows
        ]
        stage_metrics[stage] = {
            "target_hits": hit_count,
            "target_count": target_count,
            "target_recall": round(hit_count / target_count, 6),
            "questions_with_target": question_hits,
            "question_count": len(case_rows),
            "question_coverage": round(question_hits / len(case_rows), 6),
            "retained_from_previous": retained_count,
            "previous_stage_target_hits": previous_hit_count,
            "adjacent_target_retention_rate": retention_rate,
            "candidate_count_distribution": _distribution(candidate_counts),
        }
        previous_stage = stage

    target_rows = [
        target
        for case in case_rows
        for target in case["target_statuses"]
    ]
    first_loss_counts = Counter(
        target["first_loss_stage"] or "final_evidence"
        for target in target_rows
    )
    loss_reason_counts = Counter(
        target["loss_reason"] or "final_evidence" for target in target_rows
    )
    reranker_top_k_hits = sum(
        bool(target["reranker_final_k_counterfactual_hit"])
        for target in target_rows
    )
    mmr_hits = stage_metrics["mmr"]["target_hits"]
    mmr_rescued = sum(
        bool(target["present_by_stage"]["mmr"])
        and not bool(target["reranker_final_k_counterfactual_hit"])
        for target in target_rows
    )
    mmr_displaced = sum(
        not bool(target["present_by_stage"]["mmr"])
        and bool(target["reranker_final_k_counterfactual_hit"])
        for target in target_rows
    )
    rank_changes = [
        int(target["rerank_rank_change_from_hybrid"])
        for target in target_rows
        if target["rerank_rank_change_from_hybrid"] is not None
    ]
    return {
        "stage_metrics": stage_metrics,
        "first_loss_counts": dict(sorted(first_loss_counts.items())),
        "loss_reason_counts": dict(sorted(loss_reason_counts.items())),
        "reranker_mmr_transition": {
            "reranker_top5_target_hits": reranker_top_k_hits,
            "mmr_target_hits": mmr_hits,
            "targets_rescued_by_mmr_from_reranker_rank_below_top5": mmr_rescued,
            "reranker_top5_targets_displaced_by_mmr": mmr_displaced,
            "reranker_top2_guaranteed_target_hits": sum(
                bool(target["guaranteed_by_reranker_top2"])
                for target in target_rows
            ),
            "reranker_top2_guaranteed_targets_retained": sum(
                bool(target["guaranteed_by_reranker_top2"])
                and bool(target["present_by_stage"]["mmr"])
                for target in target_rows
            ),
        },
        "target_rerank_rank_change": {
            "count": len(rank_changes),
            "mean": round(statistics.fmean(rank_changes), 6)
            if rank_changes
            else None,
            "median": round(statistics.median(rank_changes), 6)
            if rank_changes
            else None,
            "improved": sum(value < 0 for value in rank_changes),
            "unchanged": sum(value == 0 for value in rank_changes),
            "worsened": sum(value > 0 for value in rank_changes),
        },
    }


def _per_paper_summary(
    case_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in case_rows:
        grouped[int(row["paper_id"])].append(row)
    summaries: list[dict[str, Any]] = []
    for paper_id, rows in grouped.items():
        target_count = sum(len(row["target_chunk_ids"]) for row in rows)
        stages = {}
        for stage in STAGES:
            hits = sum(len(row["stages"][stage]["target_hit_ids"]) for row in rows)
            question_hits = sum(
                bool(row["stages"][stage]["target_hit_ids"]) for row in rows
            )
            stages[stage] = {
                "target_hits": hits,
                "target_count": target_count,
                "target_recall": round(hits / target_count, 6),
                "questions_with_target": question_hits,
                "question_count": len(rows),
            }
        summaries.append(
            {
                "paper_id": paper_id,
                "paper_title": rows[0]["paper_title"],
                "question_count": len(rows),
                "target_count": target_count,
                "stages": stages,
            }
        )
    return summaries


def run_experiment(
    ground_truth_path: Path,
    english_query_path: Path,
) -> dict[str, Any]:
    from my_agent_project import rag_service
    from my_agent_project.config import (
        RESEARCH_CANDIDATE_POOL_SIZE,
        RESEARCH_FINAL_SEED_COUNT,
        RESEARCH_MMR_ENABLED,
        RESEARCH_MMR_LAMBDA,
        RESEARCH_PARENT_EXPANSION_ENABLED,
        RESEARCH_PRF_ENABLED,
        RESEARCH_RERANKER_ENABLED,
        RESEARCH_RERANKER_MODEL,
    )
    from my_agent_project.context_service import (
        DEFAULT_CONTEXT_TOKEN_BUDGET,
        ContextConfig,
        pack_research_context,
    )
    from my_agent_project.db import connect

    expected_configuration = {
        "candidate_pool_size": 50,
        "final_seed_count": 5,
        "reranker_enabled": True,
        "mmr_enabled": True,
        "mmr_lambda": 0.5,
        "parent_expansion_enabled": True,
        "prf_enabled": True,
    }
    actual_configuration = {
        "candidate_pool_size": RESEARCH_CANDIDATE_POOL_SIZE,
        "final_seed_count": RESEARCH_FINAL_SEED_COUNT,
        "reranker_enabled": RESEARCH_RERANKER_ENABLED,
        "mmr_enabled": RESEARCH_MMR_ENABLED,
        "mmr_lambda": RESEARCH_MMR_LAMBDA,
        "parent_expansion_enabled": RESEARCH_PARENT_EXPANSION_ENABLED,
        "prf_enabled": RESEARCH_PRF_ENABLED,
    }
    if actual_configuration != expected_configuration:
        raise RuntimeError(
            "production retrieval configuration differs from the frozen analysis: "
            f"{actual_configuration}"
        )

    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    english_queries = load_english_queries(ground_truth_path, english_query_path)
    conn = connect()
    counts_before = _table_counts(conn)
    case_rows: list[dict[str, Any]] = []

    original_embedder = rag_service._cached_seed_embeddings
    original_build_pool = rag_service._build_candidate_pool
    original_rerank = rag_service.rerank_candidates
    original_mmr = rag_service.select_mmr_candidates
    original_parent_expansion = rag_service.expand_seed_chunks_to_parents
    capture: dict[str, Any] = {}

    def capture_build_pool(
        ranked_chunks: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        capture["hybrid_raw"] = _snapshot_stage(ranked_chunks)
        result = original_build_pool(ranked_chunks, **kwargs)
        capture["candidate_pool"] = _snapshot_stage(result)
        return result

    def capture_rerank(
        original_query: str,
        candidates: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        result = original_rerank(original_query, candidates, **kwargs)
        capture["reranker"] = _snapshot_stage(result)
        return result

    def capture_mmr(
        candidates: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected, annotated = original_mmr(candidates, **kwargs)
        capture["mmr"] = _snapshot_stage(selected)
        capture["mmr_all_annotated"] = _snapshot_stage(annotated)
        return selected, annotated

    def capture_parent_expansion(
        seed_chunks: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        expanded, diagnostics = original_parent_expansion(seed_chunks, **kwargs)
        capture["parent_expansion"] = _snapshot_stage(expanded)
        capture["parent_expansion_diagnostics"] = diagnostics
        return expanded, diagnostics

    rag_service._build_candidate_pool = capture_build_pool
    rag_service.rerank_candidates = capture_rerank
    rag_service.select_mmr_candidates = capture_mmr
    rag_service.expand_seed_chunks_to_parents = capture_parent_expansion
    try:
        for paper_spec in ground_truth["papers"]:
            paper_id = int(paper_spec["paper_id"])
            paper_title = str(paper_spec["paper_title"])
            document = conn.execute(
                """
                SELECT embedding_namespace
                FROM paper_documents
                WHERE paper_id = ?
                """,
                (paper_id,),
            ).fetchone()
            chunks = conn.execute(
                """
                SELECT id, paper_id, parent_chunk_id, page_number, chunk_index,
                       section_title, content
                FROM paper_chunks
                WHERE paper_id = ?
                ORDER BY id
                """,
                (paper_id,),
            ).fetchall()
            if document is None:
                raise RuntimeError(f"paper {paper_id} document is missing")
            if len(chunks) != int(paper_spec["expected_chunk_count"]):
                raise RuntimeError(
                    f"paper {paper_id} chunk count changed: {len(chunks)}"
                )
            chunk_metadata = {
                int(row["id"]): {
                    "chunk_id": int(row["id"]),
                    "paper_id": int(row["paper_id"]),
                    "parent_chunk_id": _optional_int(row["parent_chunk_id"]),
                    "page_number": _optional_int(row["page_number"]),
                    "chunk_index": _optional_int(row["chunk_index"]),
                    "section_title": str(row["section_title"] or ""),
                    "content_sha256": hashlib.sha256(
                        str(row["content"] or "").encode("utf-8")
                    ).hexdigest(),
                }
                for row in chunks
            }
            namespace = str(document["embedding_namespace"] or "")

            for case in paper_spec["cases"]:
                capture.clear()
                case_id = str(case["case_id"])
                original_query = str(case["question"])
                lexical_query = str(english_queries[case_id])
                query_dense, vector_source = _load_query_vector(
                    conn,
                    namespace,
                    original_query,
                )

                def frozen_embedder(texts: Sequence[str], **_kwargs: Any):
                    if list(texts) != [original_query]:
                        raise RuntimeError("production Semantic query changed")
                    return [query_dense], "frozen-query-vector"

                rag_service._cached_seed_embeddings = frozen_embedder
                retrieval_diagnostics: dict[str, Any] = {}
                retrieval_started = time.perf_counter()
                retrieved = rag_service.retrieve_relevant_chunks(
                    paper_id,
                    original_query,
                    lexical_query=lexical_query,
                    top_k=RESEARCH_FINAL_SEED_COUNT,
                    prf_enabled=RESEARCH_PRF_ENABLED,
                    candidate_pool_size=RESEARCH_CANDIDATE_POOL_SIZE,
                    reranker_enabled=RESEARCH_RERANKER_ENABLED,
                    reranker_model=RESEARCH_RERANKER_MODEL,
                    mmr_enabled=RESEARCH_MMR_ENABLED,
                    mmr_lambda=RESEARCH_MMR_LAMBDA,
                    parent_expansion_enabled=RESEARCH_PARENT_EXPANSION_ENABLED,
                    evidence_paper_title=paper_title,
                    retrieval_diagnostics=retrieval_diagnostics,
                )
                retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
                evidence = [
                    {
                        **dict(item),
                        "paper_id": paper_id,
                        "paper_title": paper_title,
                    }
                    for item in retrieved
                ]
                packed = pack_research_context(
                    evidence,
                    config=ContextConfig(
                        max_estimated_tokens=DEFAULT_CONTEXT_TOKEN_BUDGET
                    ),
                )
                allocation_items = _snapshot_stage(packed["evidence"])

                stage_items = {
                    "hybrid_raw": capture.get("hybrid_raw", []),
                    "candidate_pool": capture.get("candidate_pool", []),
                    "reranker": capture.get("reranker", []),
                    "mmr": capture.get("mmr", []),
                    "parent_expansion": capture.get("parent_expansion", []),
                    "allocation": allocation_items,
                }
                mmr_evaluations = capture.get("mmr_all_annotated", [])
                target_ids = {int(value) for value in case["target_chunk_ids"]}
                missing_target_rows = target_ids - set(chunk_metadata)
                if missing_target_rows:
                    raise RuntimeError(
                        f"case {case_id} target chunks missing: "
                        f"{sorted(missing_target_rows)}"
                    )
                target_parent_ids = {
                    target_id: chunk_metadata[target_id]["parent_chunk_id"]
                    for target_id in target_ids
                }
                parent_coverage = _expanded_target_coverage(
                    target_ids,
                    target_parent_ids,
                    stage_items["parent_expansion"],
                )
                allocation_coverage = _expanded_target_coverage(
                    target_ids,
                    target_parent_ids,
                    stage_items["allocation"],
                )
                stage_hits = {
                    stage: _exact_target_hits(target_ids, stage_items[stage])
                    for stage in (
                        "hybrid_raw",
                        "candidate_pool",
                        "reranker",
                        "mmr",
                    )
                }
                stage_hits["parent_expansion"] = set(parent_coverage)
                stage_hits["allocation"] = set(allocation_coverage)
                stages = {
                    stage: _stage_entry(stage_items[stage], stage_hits[stage])
                    for stage in STAGES
                }
                parent_status = str(
                    retrieval_diagnostics.get("parent_expansion_status") or ""
                )
                guaranteed_chunk_ids = {
                    int(value)
                    for value in retrieval_diagnostics.get(
                        "guaranteed_chunk_ids", []
                    )
                }
                target_statuses = [
                    _target_status(
                        target=chunk_metadata[target_id],
                        paper_id=paper_id,
                        stage_items=stage_items,
                        mmr_evaluations=mmr_evaluations,
                        parent_coverage=parent_coverage,
                        allocation_coverage=allocation_coverage,
                        parent_status=parent_status,
                        final_top_k=RESEARCH_FINAL_SEED_COUNT,
                        guaranteed_chunk_ids=guaranteed_chunk_ids,
                    )
                    for target_id in sorted(target_ids)
                ]
                final_selected_pages = {
                    int(item["page_number"])
                    for item in stage_items["allocation"]
                    if item.get("page_number") is not None
                }
                for target_status in target_statuses:
                    target_status["final_same_page_different_chunk"] = (
                        not target_status["final_covered"]
                        and target_status["page_number"] in final_selected_pages
                    )

                case_rows.append(
                    {
                        "case_id": case_id,
                        "paper_id": paper_id,
                        "paper_title": paper_title,
                        "question": original_query,
                        "lexical_query": lexical_query,
                        "query_vector_source": vector_source,
                        "target_chunk_ids": sorted(target_ids),
                        "retrieval_ms": round(retrieval_ms, 3),
                        "query_routing": {
                            "semantic_query": "original_chinese_query",
                            "tfidf_query": "frozen_english_lexical_query",
                        },
                        "prf": {
                            "status": retrieval_diagnostics.get("prf_status"),
                            "feedback_chunk_ids": retrieval_diagnostics.get(
                                "feedback_chunk_ids", []
                            ),
                            "feedback_terms": retrieval_diagnostics.get(
                                "feedback_terms", []
                            ),
                        },
                        "reranker_status": retrieval_diagnostics.get(
                            "reranker_status"
                        ),
                        "mmr_status": retrieval_diagnostics.get("mmr_status"),
                        "parent_expansion_status": parent_status,
                        "parent_expansion_details": retrieval_diagnostics.get(
                            "parent_expansion_details", []
                        ),
                        "mmr_candidate_evaluations": mmr_evaluations,
                        "allocation_stats": packed["stats"],
                        "stages": stages,
                        "target_statuses": target_statuses,
                        "possible_granularity_mismatch_targets": [
                            target["chunk_id"]
                            for target in target_statuses
                            if target["final_same_page_different_chunk"]
                        ],
                    }
                )
        counts_after = _table_counts(conn)
    finally:
        rag_service._cached_seed_embeddings = original_embedder
        rag_service._build_candidate_pool = original_build_pool
        rag_service.rerank_candidates = original_rerank
        rag_service.select_mmr_candidates = original_mmr
        rag_service.expand_seed_chunks_to_parents = original_parent_expansion
        conn.rollback()
        conn.close()

    target_count = sum(len(row["target_chunk_ids"]) for row in case_rows)
    parent_row_count = int(counts_before["paper_parent_chunks"])
    return {
        "version": "target-stage-drop-v1",
        "pipeline_stages": list(STAGES),
        "retrieval_configuration": {
            **actual_configuration,
            "semantic_weight": 0.75,
            "tfidf_weight": 0.25,
            "reranker_model": RESEARCH_RERANKER_MODEL,
            "final_top_k_per_paper": RESEARCH_FINAL_SEED_COUNT,
            "evidence_budget": DEFAULT_CONTEXT_TOKEN_BUDGET,
            "query_routing": "original Chinese Semantic + frozen English TF-IDF",
            "allocation_strategy": "dynamic_v1",
        },
        "dataset": {
            "paper_count": len(ground_truth["papers"]),
            "question_count": len(case_rows),
            "target_chunk_count": target_count,
            "paper_parent_chunk_rows": parent_row_count,
            "parent_matching_rule": (
                "real parent ID covers target child"
                if parent_row_count
                else "child chunk_id exact match only; no parent rows"
            ),
        },
        "summary": _summarize(case_rows, target_count=target_count),
        "per_paper": _per_paper_summary(case_rows),
        "case_rows": case_rows,
        "database_safety": {
            "counts_before": counts_before,
            "counts_after": counts_after,
            "unchanged": counts_before == counts_after,
        },
        "deepseek_called": False,
        "production_code_modified": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument(
        "--english-queries",
        type=Path,
        default=DEFAULT_ENGLISH_QUERIES,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_experiment(args.ground_truth, args.english_queries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"database_unchanged={result['database_safety']['unchanged']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

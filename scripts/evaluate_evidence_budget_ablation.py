"""Read-only 6K/12K/16K Evidence Budget ablation on frozen retrieval cases.

The production retrieval pipeline is executed once per frozen question.  Its
expanded evidence is then replayed through Dynamic Evidence Allocation v1 with
three budgets, making the budget the only experimental variable.  The script
does not call DeepSeek and does not write Business Database rows or caches.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.evaluate_retrieval_fusion_ab import (
    DEFAULT_GROUND_TRUTH,
    ROOT,
    _load_query_vector,
)
from scripts.evaluate_retrieval_query_language_ab import (
    DEFAULT_ENGLISH_QUERIES,
    load_english_queries,
)


BUDGETS = (6000, 12000, 16000)
DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "evidence_budget_ablation_v1.json"
)
PROTECTED_TABLES = (
    "papers",
    "paper_chunks",
    "paper_parent_chunks",
    "research_messages",
    "research_reports",
    "paper_summaries",
    "embedding_cache",
    "llm_response_cache",
)


def _table_counts(conn: Any) -> dict[str, int]:
    return {
        table: int(
            conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                "count"
            ]
        )
        for table in PROTECTED_TABLES
    }


def _selected_source(item: Mapping[str, Any]) -> str:
    return "parent" if item.get("parent_chunk_id") is not None else "child"


def _aggregate_budget(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evidence_counts = [int(row["evidence_count"]) for row in rows]
    selected_tokens = [int(row["selected_estimated_tokens"]) for row in rows]
    papers_with_candidates = sum(int(row["papers_with_candidates"]) for row in rows)
    papers_with_evidence = sum(int(row["papers_with_evidence"]) for row in rows)
    target_count = sum(int(row["target_count"]) for row in rows)
    target_hits = sum(int(row["target_hits"]) for row in rows)
    return {
        "case_count": len(rows),
        "avg_evidence": round(statistics.fmean(evidence_counts), 6),
        "avg_selected_estimated_tokens": round(
            statistics.fmean(selected_tokens),
            6,
        ),
        "median_selected_estimated_tokens": round(
            statistics.median(selected_tokens),
            6,
        ),
        "paper_coverage": round(
            papers_with_evidence / papers_with_candidates,
            6,
        )
        if papers_with_candidates
        else 0.0,
        "papers_with_candidates": papers_with_candidates,
        "papers_with_evidence": papers_with_evidence,
        "papers_without_evidence": papers_with_candidates - papers_with_evidence,
        "budget_drops": sum(int(row["dropped_token_budget"]) for row in rows),
        "parent_evidence": sum(int(row["parent_evidence_count"]) for row in rows),
        "child_evidence": sum(int(row["child_evidence_count"]) for row in rows),
        "target_chunk_recall": round(target_hits / target_count, 6)
        if target_count
        else None,
        "target_hits": target_hits,
        "target_count": target_count,
        "avg_allocation_ms": round(
            statistics.fmean(float(row["allocation_ms"]) for row in rows),
            6,
        ),
        "answer_quality": None,
        "citation_correctness": None,
        "model_input_tokens": None,
        "response_latency_ms": None,
    }


def run_experiment(
    ground_truth_path: Path,
    english_query_path: Path,
) -> dict[str, Any]:
    from my_agent_project import rag_service
    from my_agent_project.config import (
        RESEARCH_CANDIDATE_POOL_SIZE,
        RESEARCH_MMR_ENABLED,
        RESEARCH_MMR_LAMBDA,
        RESEARCH_PARENT_EXPANSION_ENABLED,
        RESEARCH_PRF_ENABLED,
        RESEARCH_RERANKER_ENABLED,
        RESEARCH_RERANKER_MODEL,
    )
    from my_agent_project.context_service import ContextConfig, pack_research_context
    from my_agent_project.db import connect

    expected_configuration = {
        "candidate_pool_size": 20,
        "reranker_enabled": True,
        "mmr_enabled": True,
        "mmr_lambda": 0.5,
        "parent_expansion_enabled": True,
        "prf_enabled": True,
    }
    actual_configuration = {
        "candidate_pool_size": RESEARCH_CANDIDATE_POOL_SIZE,
        "reranker_enabled": RESEARCH_RERANKER_ENABLED,
        "mmr_enabled": RESEARCH_MMR_ENABLED,
        "mmr_lambda": RESEARCH_MMR_LAMBDA,
        "parent_expansion_enabled": RESEARCH_PARENT_EXPANSION_ENABLED,
        "prf_enabled": RESEARCH_PRF_ENABLED,
    }
    if actual_configuration != expected_configuration:
        raise RuntimeError(
            "production retrieval configuration differs from the frozen ablation: "
            f"{actual_configuration}"
        )

    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    english_queries = load_english_queries(ground_truth_path, english_query_path)
    conn = connect()
    counts_before = _table_counts(conn)
    parent_row_count = counts_before["paper_parent_chunks"]
    case_rows: list[dict[str, Any]] = []
    original_embedder = rag_service._cached_seed_embeddings
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
            chunk_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM paper_chunks WHERE paper_id = ?",
                    (paper_id,),
                ).fetchone()["count"]
            )
            linked_parent_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM paper_chunks
                    WHERE paper_id = ? AND parent_chunk_id IS NOT NULL
                    """,
                    (paper_id,),
                ).fetchone()["count"]
            )
            if document is None:
                raise RuntimeError(f"paper {paper_id} document is missing")
            if chunk_count != int(paper_spec["expected_chunk_count"]):
                raise RuntimeError(
                    f"paper {paper_id} chunk count changed: {chunk_count}"
                )
            namespace = str(document["embedding_namespace"] or "")

            for case in paper_spec["cases"]:
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
                    top_k=3,
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
                target_ids = {int(value) for value in case["target_chunk_ids"]}
                budget_results: dict[str, Any] = {}
                for budget in BUDGETS:
                    allocation_started = time.perf_counter()
                    packed = pack_research_context(
                        evidence,
                        config=ContextConfig(max_estimated_tokens=budget),
                    )
                    allocation_ms = (
                        time.perf_counter() - allocation_started
                    ) * 1000
                    selected = packed["evidence"]
                    selected_ids = {int(item["chunk_id"]) for item in selected}
                    per_paper_counts = {
                        str(item["paper_id"]): int(item["selected_chunks"])
                        for item in packed["stats"]["per_paper"]
                    }
                    budget_results[str(budget)] = {
                        "evidence_count": len(selected),
                        "selected_estimated_tokens": int(
                            packed["stats"]["selected_estimated_tokens"]
                        ),
                        "per_paper_evidence_count": per_paper_counts,
                        "papers_with_candidates": int(
                            packed["stats"]["papers_with_candidates"]
                        ),
                        "papers_with_evidence": int(
                            packed["stats"]["papers_with_evidence"]
                        ),
                        "papers_without_evidence": int(
                            packed["stats"]["papers_without_evidence"]
                        ),
                        "dropped_token_budget": int(
                            packed["stats"]["dropped_token_budget"]
                        ),
                        "parent_evidence_count": sum(
                            _selected_source(item) == "parent" for item in selected
                        ),
                        "child_evidence_count": sum(
                            _selected_source(item) == "child" for item in selected
                        ),
                        "selected_chunk_ids": [
                            int(item["chunk_id"]) for item in selected
                        ],
                        "target_hits": len(selected_ids & target_ids),
                        "target_count": len(target_ids),
                        "allocation_ms": round(allocation_ms, 6),
                    }
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
                        "retrieved_chunk_ids": [
                            int(item["chunk_id"]) for item in evidence
                        ],
                        "retrieved_parent_count": sum(
                            _selected_source(item) == "parent" for item in evidence
                        ),
                        "retrieved_child_count": sum(
                            _selected_source(item) == "child" for item in evidence
                        ),
                        "linked_parent_chunk_count_in_paper": linked_parent_count,
                        "parent_expansion_status": retrieval_diagnostics.get(
                            "parent_expansion_status"
                        ),
                        "budgets": budget_results,
                    }
                )
        counts_after = _table_counts(conn)
    finally:
        rag_service._cached_seed_embeddings = original_embedder
        conn.rollback()
        conn.close()

    budget_summaries = {
        str(budget): _aggregate_budget(
            [row["budgets"][str(budget)] for row in case_rows]
        )
        for budget in BUDGETS
    }
    limited_at_6000 = [
        row["case_id"]
        for row in case_rows
        if int(row["budgets"]["6000"]["dropped_token_budget"]) > 0
        or int(row["budgets"]["6000"]["papers_without_evidence"]) > 0
    ]
    return {
        "version": "evidence-budget-ablation-v1",
        "budgets": list(BUDGETS),
        "only_variable": "ContextConfig.max_estimated_tokens",
        "retrieval_configuration": {
            **actual_configuration,
            "semantic_weight": 0.75,
            "tfidf_weight": 0.25,
            "reranker_model": RESEARCH_RERANKER_MODEL,
            "final_top_k_per_paper": 3,
            "query_routing": "original Chinese Semantic + frozen English TF-IDF",
            "allocation_strategy": "dynamic_v1",
        },
        "dataset": {
            "paper_count": len(ground_truth["papers"]),
            "question_count": len(case_rows),
            "target_chunk_count": sum(
                len(row["target_chunk_ids"]) for row in case_rows
            ),
            "paper_parent_chunk_rows": parent_row_count,
            "supports_parent_expansion_comparison": parent_row_count > 0,
            "supports_multi_paper_allocation": False,
            "supports_answer_quality": False,
            "supports_citation_correctness": False,
        },
        "summary": budget_summaries,
        "cases_limited_at_6000": limited_at_6000,
        "case_rows": case_rows,
        "database_safety": {
            "counts_before": counts_before,
            "counts_after": counts_after,
            "unchanged": counts_before == counts_after,
        },
        "deepseek_called": False,
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
    print(f"cases_limited_at_6000={result['cases_limited_at_6000']}")
    print(f"database_unchanged={result['database_safety']['unchanged']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

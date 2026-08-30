"""Validate frozen Query Routing through the production retrieval function.

The frozen English queries are supplied directly so this run measures only the
production routing/ranking implementation. It does not call DeepSeek and it
does not write Business Database rows or embedding-cache entries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.evaluate_retrieval_fusion_ab import (
    DEFAULT_GROUND_TRUTH,
    ROOT,
    _load_query_vector,
    _target_metrics,
)
from scripts.evaluate_retrieval_query_language_ab import (
    DEFAULT_ENGLISH_QUERIES,
    load_english_queries,
)


DEFAULT_EXPECTED = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "retrieval_query_routing_ab_v1_results.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "retrieval_query_routing_production_validation_v1.json"
)
PROTECTED_TABLES = (
    "papers",
    "paper_chunks",
    "research_messages",
    "research_reports",
    "paper_summaries",
    "embedding_cache",
    "llm_response_cache",
)


def _table_counts(conn: Any) -> dict[str, int]:
    return {
        table: int(
            conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
        )
        for table in PROTECTED_TABLES
    }


def run_validation(
    ground_truth_path: Path,
    english_query_path: Path,
    expected_path: Path,
) -> dict[str, Any]:
    from my_agent_project import rag_service
    from my_agent_project.db import connect

    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    english_queries = load_english_queries(ground_truth_path, english_query_path)
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_ranks = {
        (str(row["case_id"]), int(row["target_chunk_id"])): int(
            row["routing_weighted_rank"]
        )
        for row in expected["target_rows"]
    }

    conn = connect()
    counts_before = _table_counts(conn)
    target_rows: list[dict[str, Any]] = []
    original_embedder = rag_service._cached_seed_embeddings
    try:
        for paper_spec in ground_truth["papers"]:
            paper_id = int(paper_spec["paper_id"])
            document = conn.execute(
                """
                SELECT embedding_namespace
                FROM paper_documents
                WHERE paper_id = ?
                """,
                (paper_id,),
            ).fetchone()
            if document is None:
                raise RuntimeError(f"paper {paper_id} document is missing")
            namespace = str(document["embedding_namespace"] or "")
            candidate_count = int(paper_spec["expected_chunk_count"])
            for case in paper_spec["cases"]:
                case_id = str(case["case_id"])
                original_query = str(case["question"])
                lexical_query = english_queries[case_id]
                query_dense, vector_source = _load_query_vector(
                    conn,
                    namespace,
                    original_query,
                )

                def frozen_embedder(texts, **_kwargs):
                    if list(texts) != [original_query]:
                        raise RuntimeError("production Semantic query changed")
                    return [query_dense], "frozen-query-vector"

                rag_service._cached_seed_embeddings = frozen_embedder
                ranked = rag_service.retrieve_relevant_chunks(
                    paper_id,
                    original_query,
                    lexical_query=lexical_query,
                    top_k=candidate_count,
                )
                ranks = {
                    int(item["chunk_id"]): rank
                    for rank, item in enumerate(ranked, start=1)
                }
                for target_chunk_id in case["target_chunk_ids"]:
                    target_chunk_id = int(target_chunk_id)
                    if target_chunk_id not in ranks:
                        raise RuntimeError(
                            f"target chunk {target_chunk_id} missing for {case_id}"
                        )
                    expected_rank = expected_ranks[(case_id, target_chunk_id)]
                    actual_rank = ranks[target_chunk_id]
                    target_rows.append(
                        {
                            "case_id": case_id,
                            "paper_title": paper_spec["paper_title"],
                            "original_query": original_query,
                            "lexical_query": lexical_query,
                            "semantic_query_type": "original_query",
                            "tfidf_query_type": "english_retrieval_query",
                            "query_vector_source": vector_source,
                            "target_chunk_id": target_chunk_id,
                            "expected_rank": expected_rank,
                            "production_rank": actual_rank,
                            "exact_match": actual_rank == expected_rank,
                        }
                    )
    finally:
        rag_service._cached_seed_embeddings = original_embedder
        counts_after = _table_counts(conn)
        conn.close()

    metrics = _target_metrics(target_rows, "production_rank")
    expected_metrics = _target_metrics(target_rows, "expected_rank")
    return {
        "version": "retrieval-query-routing-production-validation-v1",
        "deepseek_called": False,
        "semantic_query": "original Chinese user question",
        "tfidf_query": "frozen English retrieval query",
        "weighted_formula": "0.75 * semantic + 0.25 * tfidf",
        "target_rows": target_rows,
        "production_metrics": metrics,
        "expected_experiment_metrics": expected_metrics,
        "exact_rank_matches": sum(bool(row["exact_match"]) for row in target_rows),
        "target_count": len(target_rows),
        "database_safety": {
            "counts_before": counts_before,
            "counts_after": counts_after,
            "unchanged": counts_before == counts_after,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--english-queries", type=Path, default=DEFAULT_ENGLISH_QUERIES)
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_validation(args.ground_truth, args.english_queries, args.expected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["production_metrics"], ensure_ascii=False, indent=2))
    print(
        f"exact_rank_matches={result['exact_rank_matches']}/"
        f"{result['target_count']}"
    )
    print(f"database_unchanged={result['database_safety']['unchanged']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

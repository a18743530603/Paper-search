"""Read-only A/B experiment for language-aware retrieval query routing.

Baseline A uses the original Chinese user question for both Semantic and TF-IDF.
Routing B keeps the original Chinese question for Semantic and uses the frozen
English retrieval expression only for TF-IDF. All other variables are fixed.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.evaluate_retrieval_fusion_ab import (
        DEFAULT_GROUND_TRUTH,
        ROOT,
        _target_metrics,
        run_experiment,
    )
    from scripts.evaluate_retrieval_query_language_ab import (
        DEFAULT_ENGLISH_QUERIES,
        load_english_queries,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from evaluate_retrieval_fusion_ab import (
        DEFAULT_GROUND_TRUTH,
        ROOT,
        _target_metrics,
        run_experiment,
    )
    from evaluate_retrieval_query_language_ab import (
        DEFAULT_ENGLISH_QUERIES,
        load_english_queries,
    )


DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "retrieval_query_routing_ab_v1_results.json"
)


def _comparison_rows(
    baseline_rows: list[Mapping[str, Any]],
    routed_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    routed_by_target = {
        (str(row["case_id"]), int(row["target_chunk_id"])): row
        for row in routed_rows
    }
    rows: list[dict[str, Any]] = []
    for baseline in baseline_rows:
        key = (str(baseline["case_id"]), int(baseline["target_chunk_id"]))
        routed = routed_by_target.pop(key)
        if str(baseline["question"]) != str(routed["question"]):
            raise RuntimeError(f"user question changed for {key}")
        if (
            baseline["semantic_score"] != routed["semantic_score"]
            or baseline["semantic_rank"] != routed["semantic_rank"]
        ):
            raise RuntimeError(f"Semantic route changed for {key}")
        rows.append(
            {
                "case_id": key[0],
                "question_type": baseline["question_type"],
                "task_id": baseline["task_id"],
                "paper_id": baseline["paper_id"],
                "paper_title": baseline["paper_title"],
                "user_question": baseline["question"],
                "semantic_query": routed["semantic_query"],
                "baseline_tfidf_query": baseline["tfidf_query"],
                "routed_tfidf_query": routed["tfidf_query"],
                "target_chunk_id": key[1],
                "page": baseline["page"],
                "section": baseline["section"],
                "content_hash": baseline["content_hash"],
                "content_summary": baseline["content_summary"],
                "semantic_score": baseline["semantic_score"],
                "semantic_rank": baseline["semantic_rank"],
                "chinese_tfidf_score": baseline["tfidf_score"],
                "chinese_tfidf_rank": baseline["tfidf_rank"],
                "english_tfidf_score": routed["tfidf_score"],
                "english_tfidf_rank": routed["tfidf_rank"],
                "tfidf_activated": (
                    float(baseline["tfidf_score"]) == 0
                    and float(routed["tfidf_score"]) > 0
                ),
                "baseline_weighted_score": baseline["weighted_score"],
                "baseline_weighted_rank": baseline["weighted_rank"],
                "routing_weighted_score": routed["weighted_score"],
                "routing_weighted_rank": routed["weighted_rank"],
                "rank_delta_routing_minus_baseline": (
                    int(routed["weighted_rank"])
                    - int(baseline["weighted_rank"])
                ),
            }
        )
    if routed_by_target:
        raise RuntimeError(f"unmatched routed target rows: {sorted(routed_by_target)}")
    return rows


def _aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "baseline": _target_metrics(rows, "baseline_weighted_rank"),
        "query_routing": _target_metrics(rows, "routing_weighted_rank"),
    }


def _paper_equal(per_paper: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for algorithm in ("baseline", "query_routing"):
        result[algorithm] = {}
        for metric in (
            "recall_at_3",
            "recall_at_5",
            "recall_at_10",
            "recall_at_20",
            "mean_target_rank",
            "median_target_rank",
        ):
            result[algorithm][metric] = round(
                statistics.fmean(
                    float(paper[algorithm][metric]) for paper in per_paper.values()
                ),
                6,
            )
    return result


def run_routing_experiment(
    ground_truth_path: Path,
    english_query_path: Path,
) -> dict[str, Any]:
    english_queries = load_english_queries(ground_truth_path, english_query_path)
    shared_query_vectors: dict[tuple[str, str], tuple[list[float], str]] = {}
    baseline = run_experiment(
        ground_truth_path,
        query_vector_cache=shared_query_vectors,
        experiment_version="retrieval-query-routing-ab-v1-baseline",
    )
    routed = run_experiment(
        ground_truth_path,
        tfidf_queries=english_queries,
        query_vector_cache=shared_query_vectors,
        experiment_version="retrieval-query-routing-ab-v1-routed",
    )
    rows = _comparison_rows(baseline["target_rows"], routed["target_rows"])
    per_paper = {
        title: _aggregate([row for row in rows if row["paper_title"] == title])
        for title in sorted({str(row["paper_title"]) for row in rows})
    }
    per_type = {
        question_type: _aggregate(
            [row for row in rows if row["question_type"] == question_type]
        )
        for question_type in sorted({str(row["question_type"]) for row in rows})
    }
    return {
        "experiment": {
            "version": "retrieval-query-routing-ab-v1",
            "baseline": "Chinese Semantic + Chinese TF-IDF",
            "query_routing": "Chinese Semantic + English TF-IDF",
            "weighted_formula": "0.75 * semantic + 0.25 * tfidf",
            "semantic_invariant_required": True,
            "user_questions_modified": False,
            "deepseek_called": False,
            "production_ranking_modified": False,
        },
        "paper_validation": baseline["paper_validation"],
        "english_retrieval_queries": english_queries,
        "semantic_vector_sources": {
            "baseline": baseline["question_vector_sources"],
            "query_routing": routed["question_vector_sources"],
        },
        "database_safety": {
            "baseline_run": baseline["database_safety"],
            "routing_run": routed["database_safety"],
        },
        "tfidf_target_coverage": {
            "target_count": len(rows),
            "chinese_positive_count": sum(
                float(row["chinese_tfidf_score"]) > 0 for row in rows
            ),
            "english_positive_count": sum(
                float(row["english_tfidf_score"]) > 0 for row in rows
            ),
            "zero_to_positive_count": sum(bool(row["tfidf_activated"]) for row in rows),
        },
        "target_rows": rows,
        "metrics": {
            "per_paper": per_paper,
            "all_12_questions": _aggregate(rows),
            "paper_equal_weight": _paper_equal(per_paper),
            "per_question_type": per_type,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--english-queries", type=Path, default=DEFAULT_ENGLISH_QUERIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_routing_experiment(args.ground_truth, args.english_queries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["tfidf_target_coverage"], ensure_ascii=False, indent=2))
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"target_rows={len(result['target_rows'])}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

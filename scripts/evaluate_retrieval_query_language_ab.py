"""Read-only Chinese versus English retrieval-query comparison.

The frozen Chinese user question remains untouched. Only the internal query sent
to Seed embedding and TF-IDF retrieval changes. Ground Truth, chunks, embedding
model, Weighted 0.75/0.25 fusion and metric definitions remain identical.
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
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from evaluate_retrieval_fusion_ab import (
        DEFAULT_GROUND_TRUTH,
        ROOT,
        _target_metrics,
        run_experiment,
    )


DEFAULT_ENGLISH_QUERIES = ROOT / "benchmarks" / "retrieval_queries_en_ab_v1.json"
DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "retrieval_query_language_ab_v1_results.json"
)


def _case_ids(ground_truth: Mapping[str, Any]) -> set[str]:
    return {
        str(case["case_id"])
        for paper in ground_truth["papers"]
        for case in paper["cases"]
    }


def load_english_queries(
    ground_truth_path: Path,
    query_path: Path,
) -> dict[str, str]:
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    query_document = json.loads(query_path.read_text(encoding="utf-8"))
    queries = {
        str(case_id): str(query).strip()
        for case_id, query in query_document["queries"].items()
    }
    expected = _case_ids(ground_truth)
    if set(queries) != expected:
        missing = sorted(expected - set(queries))
        extra = sorted(set(queries) - expected)
        raise ValueError(f"English retrieval-query mismatch: missing={missing}, extra={extra}")
    if any(not query for query in queries.values()):
        raise ValueError("English retrieval queries must not be empty")
    return queries


def _comparison_rows(
    original_rows: list[Mapping[str, Any]],
    english_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    english_by_target = {
        (str(row["case_id"]), int(row["target_chunk_id"])): row
        for row in english_rows
    }
    rows: list[dict[str, Any]] = []
    for original in original_rows:
        key = (str(original["case_id"]), int(original["target_chunk_id"]))
        english = english_by_target.pop(key)
        if str(original["question"]) != str(english["question"]):
            raise RuntimeError(f"user question changed for {key}")
        rows.append(
            {
                "case_id": key[0],
                "question_type": original["question_type"],
                "task_id": original["task_id"],
                "paper_id": original["paper_id"],
                "paper_title": original["paper_title"],
                "user_question": original["question"],
                "original_retrieval_query": original["retrieval_query"],
                "english_retrieval_query": english["retrieval_query"],
                "target_chunk_id": key[1],
                "page": original["page"],
                "section": original["section"],
                "content_hash": original["content_hash"],
                "content_summary": original["content_summary"],
                "original_semantic_score": original["semantic_score"],
                "original_semantic_rank": original["semantic_rank"],
                "original_tfidf_score": original["tfidf_score"],
                "original_tfidf_rank": original["tfidf_rank"],
                "original_weighted_score": original["weighted_score"],
                "original_weighted_rank": original["weighted_rank"],
                "english_semantic_score": english["semantic_score"],
                "english_semantic_rank": english["semantic_rank"],
                "english_tfidf_score": english["tfidf_score"],
                "english_tfidf_rank": english["tfidf_rank"],
                "english_weighted_score": english["weighted_score"],
                "english_weighted_rank": english["weighted_rank"],
                "rank_delta_english_minus_original": (
                    int(english["weighted_rank"])
                    - int(original["weighted_rank"])
                ),
            }
        )
    if english_by_target:
        raise RuntimeError(f"unmatched English target rows: {sorted(english_by_target)}")
    return rows


def _aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "original_chinese": _target_metrics(rows, "original_weighted_rank"),
        "english_retrieval": _target_metrics(rows, "english_weighted_rank"),
    }


def _paper_equal(per_paper: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for algorithm in ("original_chinese", "english_retrieval"):
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


def run_language_experiment(
    ground_truth_path: Path,
    english_query_path: Path,
) -> dict[str, Any]:
    english_queries = load_english_queries(ground_truth_path, english_query_path)
    original = run_experiment(
        ground_truth_path,
        experiment_version="retrieval-query-language-ab-v1-original-chinese",
    )
    english = run_experiment(
        ground_truth_path,
        retrieval_queries=english_queries,
        experiment_version="retrieval-query-language-ab-v1-english-retrieval",
    )
    rows = _comparison_rows(original["target_rows"], english["target_rows"])
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
            "version": "retrieval-query-language-ab-v1",
            "only_variable": "internal retrieval query language",
            "user_questions_modified": False,
            "weighted_formula": "0.75 * semantic + 0.25 * tfidf",
            "deepseek_called": False,
            "production_ranking_modified": False,
        },
        "paper_validation": original["paper_validation"],
        "english_retrieval_queries": english_queries,
        "query_vector_sources": {
            "original_chinese": original["question_vector_sources"],
            "english_retrieval": english["question_vector_sources"],
        },
        "database_safety": {
            "original_run": original["database_safety"],
            "english_run": english["database_safety"],
        },
        "tfidf_target_coverage": {
            "target_count": len(rows),
            "original_positive_count": sum(
                float(row["original_tfidf_score"]) > 0 for row in rows
            ),
            "english_positive_count": sum(
                float(row["english_tfidf_score"]) > 0 for row in rows
            ),
            "original_zero_count": sum(
                float(row["original_tfidf_score"]) == 0 for row in rows
            ),
            "english_zero_count": sum(
                float(row["english_tfidf_score"]) == 0 for row in rows
            ),
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
    result = run_language_experiment(args.ground_truth, args.english_queries)
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

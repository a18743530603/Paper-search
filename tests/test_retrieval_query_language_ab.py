import json
from pathlib import Path

from scripts.evaluate_retrieval_query_language_ab import (
    _aggregate,
    _comparison_rows,
    load_english_queries,
)


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_english_queries_cover_all_twelve_cases_exactly():
    queries = load_english_queries(
        ROOT / "benchmarks" / "retrieval_fusion_ab_v1.json",
        ROOT / "benchmarks" / "retrieval_queries_en_ab_v1.json",
    )

    assert len(queries) == 12
    assert queries["ict-metrics"] == "evaluation metrics used in the experiments"


def test_comparison_preserves_user_question_and_records_rank_delta():
    shared = {
        "case_id": "case",
        "question_type": "metric",
        "task_id": 1,
        "paper_id": 2,
        "paper_title": "Paper",
        "question": "原问题",
        "target_chunk_id": 3,
        "page": 4,
        "section": "Experiments",
        "content_hash": "hash",
        "content_summary": "evidence",
        "semantic_score": 0.5,
        "semantic_rank": 10,
        "tfidf_score": 0.0,
        "tfidf_rank": None,
        "weighted_score": 0.375,
        "weighted_rank": 10,
    }
    original = {**shared, "retrieval_query": "原问题"}
    english = {
        **shared,
        "retrieval_query": "evaluation metrics",
        "semantic_rank": 5,
        "tfidf_score": 0.2,
        "tfidf_rank": 3,
        "weighted_rank": 4,
    }

    row = _comparison_rows([original], [english])[0]

    assert row["user_question"] == "原问题"
    assert row["english_retrieval_query"] == "evaluation metrics"
    assert row["rank_delta_english_minus_original"] == -6


def test_language_metrics_compare_same_target_rows():
    rows = [
        {
            "case_id": "one",
            "original_weighted_rank": 20,
            "english_weighted_rank": 2,
        },
        {
            "case_id": "two",
            "original_weighted_rank": 10,
            "english_weighted_rank": 4,
        },
    ]

    metrics = _aggregate(rows)

    assert metrics["original_chinese"]["recall_at_3"] == 0.0
    assert metrics["english_retrieval"]["recall_at_3"] == 0.5

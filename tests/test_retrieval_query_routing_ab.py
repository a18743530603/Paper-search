import pytest

from scripts.evaluate_retrieval_fusion_ab import _score_paper_question
from scripts.evaluate_retrieval_query_routing_ab import _comparison_rows


def _chunk(chunk_id, index, content, sparse, dense):
    import json

    return {
        "id": chunk_id,
        "chunk_index": index,
        "page_number": 1,
        "section_title": "Experiments",
        "content": content,
        "vector_json": json.dumps(sparse),
        "dense_vector_json": json.dumps(dense),
    }


def test_tfidf_query_can_be_routed_without_changing_semantic_scores():
    chunks = [
        _chunk(1, 0, "evaluation metrics", {"evaluation": 1.0, "metrics": 1.0}, [1.0, 0.0]),
        _chunk(2, 1, "training method", {"training": 1.0, "method": 1.0}, [0.0, 1.0]),
    ]
    baseline = _score_paper_question(chunks, "评价指标", [1.0, 0.0])
    routed = _score_paper_question(
        chunks,
        "评价指标",
        [1.0, 0.0],
        tfidf_question="evaluation metrics",
    )
    baseline_by_id = {row["chunk_id"]: row for row in baseline}
    routed_by_id = {row["chunk_id"]: row for row in routed}

    assert baseline_by_id[1]["semantic_score"] == routed_by_id[1]["semantic_score"]
    assert baseline_by_id[1]["semantic_rank"] == routed_by_id[1]["semantic_rank"]
    assert baseline_by_id[1]["tfidf_score"] == 0.0
    assert routed_by_id[1]["tfidf_score"] > 0.0


def test_comparison_rejects_any_semantic_change():
    shared = {
        "case_id": "case",
        "question_type": "metric",
        "task_id": 1,
        "paper_id": 2,
        "paper_title": "Paper",
        "question": "评价指标？",
        "semantic_query": "评价指标？",
        "tfidf_query": "评价指标？",
        "target_chunk_id": 3,
        "page": 1,
        "section": "Experiments",
        "content_hash": "hash",
        "content_summary": "evidence",
        "semantic_score": 0.5,
        "semantic_rank": 1,
        "tfidf_score": 0.0,
        "tfidf_rank": None,
        "weighted_score": 0.375,
        "weighted_rank": 1,
    }
    routed = {
        **shared,
        "tfidf_query": "evaluation metrics",
        "semantic_score": 0.4,
    }

    with pytest.raises(RuntimeError, match="Semantic route changed"):
        _comparison_rows([shared], [routed])

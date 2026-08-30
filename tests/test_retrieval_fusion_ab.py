from scripts.evaluate_retrieval_fusion_ab import _aggregate, rank_fusion_candidates


def _candidate(chunk_id, chunk_index, semantic, tfidf):
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "semantic_score": semantic,
        "tfidf_score": tfidf,
    }


def test_weighted_score_matches_existing_point_seven_five_point_two_five_formula():
    ranked = rank_fusion_candidates(
        [
            _candidate(1, 0, 0.8, 0.2),
            _candidate(2, 1, 0.4, 0.9),
        ]
    )
    by_id = {item["chunk_id"]: item for item in ranked}

    assert by_id[1]["weighted_score"] == 0.65
    assert by_id[2]["weighted_score"] == 0.525
    assert by_id[1]["weighted_rank"] == 1
    assert by_id[2]["weighted_rank"] == 2


def test_rrf_uses_independent_one_based_semantic_and_tfidf_ranks():
    ranked = rank_fusion_candidates(
        [
            _candidate(1, 0, 0.9, 0.1),
            _candidate(2, 1, 0.8, 0.8),
            _candidate(3, 2, 0.1, 0.9),
        ],
        rrf_k=60,
    )
    by_id = {item["chunk_id"]: item for item in ranked}

    assert by_id[2]["semantic_rank"] == 2
    assert by_id[2]["tfidf_rank"] == 2
    assert by_id[2]["rrf_score"] == round(2 / 62, 9)
    assert by_id[2]["rrf_rank"] == 3


def test_rrf_ties_use_semantic_then_tfidf_then_stable_chunk_order():
    ranked = rank_fusion_candidates(
        [
            _candidate(30, 0, 0.8, 0.3),
            _candidate(20, 1, 0.7, 0.4),
            _candidate(10, 2, 0.9, 0.2),
        ],
        rrf_k=60,
    )
    by_id = {item["chunk_id"]: item for item in ranked}

    # Chunks 10 and 20 have equal reciprocal-rank sums; semantic rank wins.
    assert by_id[10]["rrf_score"] == by_id[20]["rrf_score"]
    assert by_id[10]["rrf_rank"] < by_id[20]["rrf_rank"]


def test_zero_tfidf_score_has_no_keyword_rank_or_rrf_contribution():
    ranked = rank_fusion_candidates(
        [
            _candidate(1, 0, 0.9, 0.0),
            _candidate(2, 1, 0.8, 0.2),
            _candidate(3, 2, 0.7, 0.0),
        ],
        rrf_k=60,
    )
    by_id = {item["chunk_id"]: item for item in ranked}

    assert by_id[1]["tfidf_rank"] is None
    assert by_id[1]["rrf_score"] == round(1 / 61, 9)
    assert by_id[2]["tfidf_rank"] == 1
    assert by_id[2]["rrf_score"] == round(1 / 62 + 1 / 61, 9)
    assert by_id[3]["tfidf_rank"] is None


def test_recall_is_macro_averaged_by_question_not_pooled_by_target_count():
    rows = [
        {"case_id": "one-target", "weighted_rank": 1, "rrf_rank": 1},
        {"case_id": "two-targets", "weighted_rank": 1, "rrf_rank": 1},
        {"case_id": "two-targets", "weighted_rank": 100, "rrf_rank": 100},
    ]

    metrics = _aggregate(rows)

    # Per-question recall is mean(1/1, 1/2) = 0.75, not pooled 2/3.
    assert metrics["weighted"]["recall_at_3"] == 0.75
    assert metrics["weighted"]["question_count"] == 2
    assert metrics["weighted"]["target_count"] == 3

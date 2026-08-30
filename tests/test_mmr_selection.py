import json
import math

import pytest

from my_agent_project import config, mmr_service, rag_service, research_tools


def _candidate(
    chunk_id: int,
    rerank_rank: int,
    rerank_score: float,
    embedding,
):
    return {
        "chunk_id": chunk_id,
        "paper_id": 7,
        "page_number": chunk_id,
        "physical_page": chunk_id,
        "chunk_index": chunk_id - 1,
        "section_title": "Results",
        "content": f"original paper chunk {chunk_id}",
        "semantic_score": 0.8,
        "keyword_score": 0.4,
        "tfidf_score": 0.4,
        "score": 0.7,
        "hybrid_score": 0.7,
        "hybrid_rank": rerank_rank,
        "rerank_score": rerank_score,
        "rerank_rank": rerank_rank,
        mmr_service.MMR_EMBEDDING_FIELD: embedding,
    }


def _row(chunk_id: int, embedding, *, dense_json=True):
    content = f"metric evidence chunk {chunk_id}"
    return {
        "id": chunk_id,
        "page_number": chunk_id,
        "chunk_index": chunk_id - 1,
        "content": content,
        "vector_json": json.dumps(rag_service.term_frequency(content)),
        "dense_vector_json": json.dumps(embedding) if dense_json else None,
        "parent_chunk_id": None,
        "parent_content": None,
        "parent_page_number": None,
        "parent_index": None,
        "section_title": "Results",
        "content_type": "text",
        "bbox_json": None,
        "chunk_role": "retrieval",
    }


def _install_retrieval(monkeypatch, rows):
    calls = {"embedding": 0}
    monkeypatch.setattr(rag_service, "get_document", lambda _paper_id: None)
    monkeypatch.setattr(rag_service, "list_indexed_chunks", lambda _paper_id: rows)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)

    def embed(texts, **_kwargs):
        calls["embedding"] += 1
        return [[1.0, 0.0, 0.0] for _text in texts], "seed-test"

    monkeypatch.setattr(rag_service, "_cached_seed_embeddings", embed)
    return calls


def _identity_reranker(_query, candidates, **_kwargs):
    reranked = []
    count = len(candidates)
    for rank, candidate in enumerate(candidates, start=1):
        item = dict(candidate)
        item["rerank_score"] = float(count - rank + 1)
        item["rerank_rank"] = rank
        reranked.append(item)
    return reranked


def test_mmr_guarantees_reranker_top_two_even_when_they_are_redundant():
    candidates = [
        _candidate(1, 1, 1.0, [1.0, 0.0]),
        _candidate(2, 2, 0.9, [0.999, 0.001]),
        _candidate(3, 3, 0.8, [0.0, 1.0]),
    ]

    selected, annotated = mmr_service.select_mmr_candidates(
        candidates,
        final_top_k=2,
        lambda_value=0.5,
    )

    assert [item["chunk_id"] for item in selected] == [1, 2]
    assert selected[0]["rerank_rank"] == 1
    assert selected[1]["rerank_rank"] == 2
    assert selected[1]["mmr_max_similarity"] > 0.99
    assert next(item for item in annotated if item["chunk_id"] == 3)[
        "mmr_rank"
    ] is None


def test_mmr_can_select_rerank_rank_eight_from_the_full_pool():
    candidates = [_candidate(1, 1, 1.0, [1.0, 0.0])]
    candidates.extend(
        _candidate(index, index, 1.0 - index * 0.01, [1.0, 0.0])
        for index in range(2, 8)
    )
    candidates.append(_candidate(8, 8, 0.5, [0.0, 1.0]))
    candidates.extend(
        _candidate(index, index, 0.4 - index * 0.01, [1.0, 0.0])
        for index in range(9, 11)
    )

    selected, _annotated = mmr_service.select_mmr_candidates(
        candidates,
        final_top_k=3,
        lambda_value=0.5,
    )

    assert [item["chunk_id"] for item in selected] == [1, 2, 8]


def test_mmr_returns_exact_final_top_k_from_twenty_candidates():
    candidates = [
        _candidate(
            index,
            index,
            21.0 - index,
            [math.cos(index), math.sin(index)],
        )
        for index in range(1, 21)
    ]

    selected, annotated = mmr_service.select_mmr_candidates(
        candidates,
        final_top_k=5,
        lambda_value=0.5,
    )

    assert len(selected) == 5
    assert len(annotated) == 20
    assert [item["chunk_id"] for item in selected[:2]] == [1, 2]
    assert [item["mmr_rank"] for item in selected] == [1, 2, 3, 4, 5]


def test_mmr_fill_redundancy_compares_against_both_guaranteed_candidates():
    candidates = [
        _candidate(1, 1, 1.0, [1.0, 0.0, 0.0]),
        _candidate(2, 2, 0.9, [0.0, 1.0, 0.0]),
        _candidate(3, 3, 0.8, [1.0, 0.0, 0.0]),
        _candidate(4, 4, 0.7, [0.0, 0.0, 1.0]),
    ]

    selected, annotated = mmr_service.select_mmr_candidates(
        candidates,
        final_top_k=3,
        lambda_value=0.5,
    )

    assert [item["chunk_id"] for item in selected] == [1, 2, 4]
    candidate_three = next(item for item in annotated if item["chunk_id"] == 3)
    assert candidate_three["mmr_max_similarity"] == pytest.approx(1.0)


def test_mmr_tie_breaking_is_stable_by_rerank_rank_then_input_order():
    candidates = [
        _candidate(1, 1, 0.5, [1.0, 0.0, 0.0]),
        _candidate(2, 2, 0.5, [0.0, 1.0, 0.0]),
        _candidate(3, 3, 0.5, [0.0, 0.0, 1.0]),
    ]

    first, _ = mmr_service.select_mmr_candidates(
        candidates,
        final_top_k=2,
        lambda_value=0.0,
    )
    second, _ = mmr_service.select_mmr_candidates(
        candidates,
        final_top_k=2,
        lambda_value=0.0,
    )

    assert [item["chunk_id"] for item in first] == [1, 2]
    assert first == second


def test_mmr_disabled_exactly_preserves_reranker_v1(monkeypatch):
    rows = [_row(index, [1.0, 0.0, 0.0]) for index in range(1, 8)]
    _install_retrieval(monkeypatch, rows)
    monkeypatch.setattr(rag_service, "rerank_candidates", _identity_reranker)
    monkeypatch.setattr(
        rag_service,
        "select_mmr_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled MMR must not run")
        ),
    )

    baseline = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=True,
    )
    diagnostics = {}
    disabled = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=False,
        retrieval_diagnostics=diagnostics,
    )

    assert disabled == baseline
    assert diagnostics["mmr_status"] == "disabled"


def test_missing_embedding_falls_back_to_rerank_order(monkeypatch):
    rows = [
        _row(1, [1.0, 0.0, 0.0]),
        _row(2, [0.9, 0.1, 0.0], dense_json=False),
        _row(3, [0.0, 1.0, 0.0]),
        _row(4, [0.0, 0.0, 1.0]),
    ]
    _install_retrieval(monkeypatch, rows)
    monkeypatch.setattr(rag_service, "rerank_candidates", _identity_reranker)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=2,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in evidence] == [1, 2]
    assert diagnostics["mmr_status"] == "fallback_invalid_embeddings"
    assert "embedding" in diagnostics["mmr_error"]


def test_pool_smaller_than_final_five_returns_available_candidates(monkeypatch):
    rows = [
        _row(1, [1.0, 0.0, 0.0]),
        _row(2, [0.0, 1.0, 0.0]),
    ]
    _install_retrieval(monkeypatch, rows)
    monkeypatch.setattr(rag_service, "rerank_candidates", _identity_reranker)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=5,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in evidence] == [1, 2]
    assert diagnostics["mmr_status"] == "applied"
    assert diagnostics["guaranteed_chunk_ids"] == [1, 2]
    assert diagnostics["final_seed_chunk_ids"] == [1, 2]


def test_unexpected_mmr_error_falls_back_without_failing_retrieval(monkeypatch):
    rows = [_row(index, [1.0, 0.1 * index, 0.0]) for index in range(1, 5)]
    _install_retrieval(monkeypatch, rows)
    monkeypatch.setattr(rag_service, "rerank_candidates", _identity_reranker)
    monkeypatch.setattr(
        rag_service,
        "select_mmr_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("selection runtime failure")
        ),
    )
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=2,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in evidence] == [1, 2]
    assert diagnostics["mmr_status"] == "fallback_error"
    assert "selection runtime failure" in diagnostics["mmr_error"]


def test_mmr_pipeline_uses_existing_dense_vectors_without_extra_model_calls(
    monkeypatch,
):
    rows = [
        _row(index, [1.0, float(index) / 20.0, 0.1])
        for index in range(1, 21)
    ]
    calls = _install_retrieval(monkeypatch, rows)
    monkeypatch.setattr(rag_service, "rerank_candidates", _identity_reranker)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=5,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert len(evidence) == 5
    assert calls["embedding"] == 1
    assert diagnostics["mmr_status"] == "applied"
    assert diagnostics["mmr_selected_count"] == 5
    assert diagnostics["guaranteed_rerank_count"] == 2
    assert diagnostics["guaranteed_chunk_ids"] == [1, 2]
    assert diagnostics["final_seed_count"] == 5
    assert len(diagnostics["final_seed_chunk_ids"]) == 5


def test_mmr_internal_fields_and_vectors_never_reach_evidence_or_diagnostics(
    monkeypatch,
):
    rows = [
        _row(index, [1.0, float(index) / 10.0, 0.1])
        for index in range(1, 7)
    ]
    _install_retrieval(monkeypatch, rows)
    monkeypatch.setattr(rag_service, "rerank_candidates", _identity_reranker)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    forbidden = {
        mmr_service.MMR_EMBEDDING_FIELD,
        "mmr_score",
        "mmr_rank",
        "mmr_max_similarity",
        "mmr_relevance",
        "rerank_score",
        "rerank_rank",
        "hybrid_score",
        "hybrid_rank",
    }
    assert all(not forbidden.intersection(item) for item in evidence)
    serialized_diagnostics = json.dumps(diagnostics, ensure_ascii=False)
    assert mmr_service.MMR_EMBEDDING_FIELD not in serialized_diagnostics
    assert "original paper chunk" not in serialized_diagnostics
    assert diagnostics["mmr_selected_chunk_ids"]
    assert diagnostics["candidate_mmr_details"]


@pytest.mark.parametrize("value", ["bad", "-0.1", "1.1"])
def test_mmr_lambda_configuration_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="RESEARCH_MMR_LAMBDA"):
        config._unit_interval_float_setting(
            {"RESEARCH_MMR_LAMBDA": value},
            "RESEARCH_MMR_LAMBDA",
            0.5,
        )


def test_research_tool_passes_mmr_configuration(monkeypatch):
    captured = {}
    monkeypatch.setattr(research_tools, "RESEARCH_MMR_ENABLED", True)
    monkeypatch.setattr(research_tools, "RESEARCH_MMR_LAMBDA", 0.5)
    monkeypatch.setattr(
        research_tools,
        "get_paper",
        lambda _paper_id: {"id": 1, "title": "Paper", "page_url": ""},
    )

    def retrieve(_paper_id, _question, **kwargs):
        captured.update(kwargs)
        return [{"chunk_id": 1, "page_number": 1, "content": "evidence"}]

    monkeypatch.setattr(research_tools, "retrieve_relevant_chunks", retrieve)
    result = research_tools.retrieve_paper_evidence_with_lexical_query(
        1,
        "原问题",
        lexical_query="english query",
    )

    assert captured["mmr_enabled"] is True
    assert captured["mmr_lambda"] == 0.5
    assert result["input"]["mmr_enabled"] is True
    assert result["input"]["mmr_lambda"] == 0.5

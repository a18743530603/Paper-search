import json
import math

import pytest

from my_agent_project import rag_service, reranker_service, research_tools


def _candidate(chunk_id: int, hybrid_rank: int, content: str):
    return {
        "chunk_id": chunk_id,
        "paper_id": 7,
        "page_number": chunk_id,
        "physical_page": chunk_id,
        "section_title": "Results",
        "content": content,
        "semantic_score": 0.8,
        "keyword_score": 0.4,
        "tfidf_score": 0.4,
        "score": 0.7,
        "hybrid_score": 0.7,
        "hybrid_rank": hybrid_rank,
    }


def _row(chunk_id: int, semantic_score: float):
    content = f"original paper chunk {chunk_id}"
    return {
        "id": chunk_id,
        "page_number": chunk_id,
        "chunk_index": chunk_id - 1,
        "content": content,
        "vector_json": json.dumps(rag_service.term_frequency(content)),
        "dense_vector_json": json.dumps(
            [semantic_score, math.sqrt(max(0.0, 1.0 - semantic_score**2))]
        ),
        "parent_chunk_id": None,
        "parent_content": None,
        "parent_page_number": None,
        "parent_index": None,
        "section_title": "Results",
        "content_type": "text",
        "bbox_json": None,
        "chunk_role": "retrieval",
    }


def _install_retrieval(monkeypatch, count: int = 25):
    rows = [
        _row(index, 1.0 - (index - 1) * 0.02)
        for index in range(1, count + 1)
    ]
    monkeypatch.setattr(rag_service, "get_document", lambda _paper_id: None)
    monkeypatch.setattr(rag_service, "list_indexed_chunks", lambda _paper_id: rows)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)
    monkeypatch.setattr(
        rag_service,
        "_cached_seed_embeddings",
        lambda texts, **_kwargs: ([[1.0, 0.0] for _text in texts], "seed-test"),
    )


def test_cross_encoder_reorders_candidates_by_score_and_keeps_hybrid_rank(monkeypatch):
    captured = {}

    class FakeModel:
        def compute_score(self, pairs, **kwargs):
            captured["pairs"] = pairs
            captured["kwargs"] = kwargs
            return [0.1, 0.9, 0.4]

    monkeypatch.setattr(reranker_service, "_get_reranker", lambda _model: FakeModel())
    candidates = [
        _candidate(1, 1, "A"),
        _candidate(2, 2, "B"),
        _candidate(3, 3, "C"),
    ]

    reranked = reranker_service.rerank_candidates(
        "这篇论文使用了什么评价指标？",
        candidates,
        model_name="BAAI/bge-reranker-v2-m3",
    )

    assert [item["chunk_id"] for item in reranked] == [2, 3, 1]
    assert [item["rerank_rank"] for item in reranked] == [1, 2, 3]
    assert [item["hybrid_rank"] for item in reranked] == [2, 3, 1]
    assert captured["pairs"] == [
        ["这篇论文使用了什么评价指标？", "A"],
        ["这篇论文使用了什么评价指标？", "B"],
        ["这篇论文使用了什么评价指标？", "C"],
    ]
    assert captured["kwargs"]["normalize"] is True


def test_equal_reranker_scores_use_hybrid_rank_as_stable_tie_breaker(monkeypatch):
    class FakeModel:
        def compute_score(self, _pairs, **_kwargs):
            return [0.5, 0.5, 0.5]

    monkeypatch.setattr(reranker_service, "_get_reranker", lambda _model: FakeModel())
    candidates = [
        _candidate(1, 1, "A"),
        _candidate(2, 2, "B"),
        _candidate(3, 3, "C"),
    ]

    reranked = reranker_service.rerank_candidates(
        "原始问题",
        candidates,
        model_name="BAAI/bge-reranker-v2-m3",
    )

    assert [item["chunk_id"] for item in reranked] == [1, 2, 3]
    assert [item["rerank_rank"] for item in reranked] == [1, 2, 3]


def test_reranker_only_sees_top_twenty_and_uses_original_query(monkeypatch):
    _install_retrieval(monkeypatch, 25)
    captured = {}

    def rerank(original_query, candidates, **_kwargs):
        captured["query"] = original_query
        captured["chunk_ids"] = [item["chunk_id"] for item in candidates]
        reranked = []
        for rerank_rank, candidate in enumerate(reversed(candidates), start=1):
            item = dict(candidate)
            item["rerank_score"] = float(rerank_rank)
            item["rerank_rank"] = rerank_rank
            reranked.append(item)
        return reranked

    monkeypatch.setattr(rag_service, "rerank_candidates", rerank)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "原始中文问题",
        lexical_query="english lexical query",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=True,
        reranker_model="BAAI/bge-reranker-v2-m3",
        retrieval_diagnostics=diagnostics,
    )

    assert captured["query"] == "原始中文问题"
    assert captured["chunk_ids"] == list(range(1, 21))
    assert 21 not in captured["chunk_ids"]
    assert len(evidence) == 3
    assert diagnostics["candidate_pool_size"] == 20
    assert diagnostics["reranker_status"] == "applied"
    assert diagnostics["reranked_candidate_count"] == 20


def test_reranker_disabled_matches_candidate_pool_v1(monkeypatch):
    _install_retrieval(monkeypatch, 25)
    monkeypatch.setattr(
        rag_service,
        "rerank_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled reranker must not run")
        ),
    )
    baseline = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
    )
    diagnostics = {}

    disabled = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=False,
        reranker_model="BAAI/bge-reranker-v2-m3",
        retrieval_diagnostics=diagnostics,
    )

    assert disabled == baseline
    assert diagnostics["reranker_enabled"] is False
    assert diagnostics["reranker_status"] == "disabled"
    assert diagnostics["reranked_candidate_count"] == 0


def test_reranker_failure_falls_back_to_hybrid_without_failing_retrieval(monkeypatch):
    _install_retrieval(monkeypatch, 25)
    monkeypatch.setattr(
        rag_service,
        "rerank_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("model unavailable")
        ),
    )
    baseline = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
    )
    diagnostics = {}

    fallback = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=True,
        reranker_model="BAAI/bge-reranker-v2-m3",
        retrieval_diagnostics=diagnostics,
    )

    assert fallback == baseline
    assert diagnostics["reranker_status"] == "fallback_error"
    assert "model unavailable" in diagnostics["reranker_error"]
    assert diagnostics["reranked_candidate_count"] == 0


def test_reranker_singleton_loads_model_once(monkeypatch):
    reranker_service._reset_reranker_cache()
    created = []
    model = object()
    monkeypatch.setattr(
        reranker_service,
        "_create_reranker",
        lambda model_name: created.append(model_name) or model,
    )

    first = reranker_service._get_reranker("BAAI/bge-reranker-v2-m3")
    second = reranker_service._get_reranker("BAAI/bge-reranker-v2-m3")

    assert first is model
    assert second is model
    assert created == ["BAAI/bge-reranker-v2-m3"]
    reranker_service._reset_reranker_cache()


@pytest.mark.parametrize("scores", [[], [0.1], [0.1, float("nan")]])
def test_invalid_reranker_scores_are_rejected(monkeypatch, scores):
    class FakeModel:
        def compute_score(self, _pairs, **_kwargs):
            return scores

    monkeypatch.setattr(reranker_service, "_get_reranker", lambda _model: FakeModel())

    with pytest.raises(ValueError, match="reranker returned"):
        reranker_service.rerank_candidates(
            "原始问题",
            [_candidate(1, 1, "A"), _candidate(2, 2, "B")],
            model_name="BAAI/bge-reranker-v2-m3",
        )


def test_reranker_diagnostics_are_complete_and_not_evidence(monkeypatch):
    _install_retrieval(monkeypatch, 4)

    def rerank(_query, candidates, **_kwargs):
        reranked = []
        scores = [0.1, 0.9, 0.4, 0.2]
        for candidate, score in zip(candidates, scores):
            item = dict(candidate)
            item["rerank_score"] = score
            reranked.append(item)
        reranked.sort(key=lambda item: -item["rerank_score"])
        for rank, item in enumerate(reranked, start=1):
            item["rerank_rank"] = rank
        return reranked

    monkeypatch.setattr(rag_service, "rerank_candidates", rerank)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
        reranker_enabled=True,
        reranker_model="BAAI/bge-reranker-v2-m3",
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in evidence] == [2, 3, 4]
    assert diagnostics["reranker_enabled"] is True
    assert diagnostics["reranker_status"] == "applied"
    assert diagnostics["reranker_model"] == "BAAI/bge-reranker-v2-m3"
    assert diagnostics["reranked_candidate_count"] == 4
    assert diagnostics["reranker_error"] == ""
    assert diagnostics["candidate_rerank_details"][1] == {
        "chunk_id": 2,
        "hybrid_rank": 2,
        "rerank_rank": 1,
        "hybrid_score": diagnostics["candidate_rerank_details"][1]["hybrid_score"],
        "rerank_score": 0.9,
    }
    for item in evidence:
        assert "hybrid_rank" not in item
        assert "hybrid_score" not in item
        assert "rerank_rank" not in item
        assert "rerank_score" not in item


def test_research_tool_passes_reranker_configuration(monkeypatch):
    captured = {}
    monkeypatch.setattr(research_tools, "RESEARCH_CANDIDATE_POOL_SIZE", 20)
    monkeypatch.setattr(research_tools, "RESEARCH_RERANKER_ENABLED", True)
    monkeypatch.setattr(
        research_tools,
        "RESEARCH_RERANKER_MODEL",
        "BAAI/bge-reranker-v2-m3",
    )
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

    assert captured["reranker_enabled"] is True
    assert captured["reranker_model"] == "BAAI/bge-reranker-v2-m3"
    assert result["input"]["reranker_enabled"] is True
    assert result["input"]["reranker_model"] == "BAAI/bge-reranker-v2-m3"

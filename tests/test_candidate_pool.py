import json
import math

import pytest

from my_agent_project import config, rag_service, research_tools


def _row(chunk_id: int, semantic_score: float, *, content: str | None = None):
    return {
        "id": chunk_id,
        "page_number": chunk_id,
        "chunk_index": chunk_id - 1,
        "content": content or f"body chunk {chunk_id}",
        "vector_json": json.dumps(
            rag_service.term_frequency(content or f"body chunk {chunk_id}")
        ),
        "dense_vector_json": json.dumps(
            [semantic_score, math.sqrt(max(0.0, 1.0 - semantic_score**2))]
        ),
        "parent_chunk_id": None,
        "parent_content": None,
        "parent_page_number": None,
        "parent_index": None,
        "section_title": "Experiments",
        "content_type": "text",
        "bbox_json": None,
        "chunk_role": "retrieval",
    }


def _rows(count: int):
    return [_row(index, 1.0 - (index - 1) * 0.02) for index in range(1, count + 1)]


def _install_retrieval(monkeypatch, rows):
    monkeypatch.setattr(rag_service, "get_document", lambda _paper_id: None)
    monkeypatch.setattr(rag_service, "list_indexed_chunks", lambda _paper_id: rows)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)
    monkeypatch.setattr(
        rag_service,
        "_cached_seed_embeddings",
        lambda texts, **_kwargs: ([[1.0, 0.0] for _text in texts], "seed-test"),
    )


def test_research_selection_production_defaults_are_top50_and_final5():
    assert config.RESEARCH_CANDIDATE_POOL_SIZE == 50
    assert config.RESEARCH_FINAL_SEED_COUNT == 5


def test_candidate_pool_limits_more_than_twenty_chunks(monkeypatch):
    _install_retrieval(monkeypatch, _rows(25))
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        lexical_query="unmatched lexical query",
        top_k=3,
        candidate_pool_size=20,
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in evidence] == [1, 2, 3]
    assert diagnostics["candidate_pool_limit"] == 20
    assert diagnostics["candidate_pool_size"] == 20
    assert diagnostics["candidate_chunk_ids"] == list(range(1, 21))
    assert diagnostics["candidate_hybrid_ranks"] == list(range(1, 21))


def test_candidate_pool_uses_all_chunks_when_paper_is_smaller(monkeypatch):
    _install_retrieval(monkeypatch, _rows(12))
    diagnostics = {}

    rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
        retrieval_diagnostics=diagnostics,
    )

    assert diagnostics["candidate_pool_size"] == 12
    assert diagnostics["candidate_chunk_ids"] == list(range(1, 13))


def test_candidate_contains_required_hybrid_metadata():
    candidates = rag_service._build_candidate_pool(
        [
            {
                "chunk_id": 11,
                "page_number": 4,
                "section_title": "Results",
                "content": "Original paper body",
                "semantic_score": 0.8,
                "keyword_score": 0.4,
                "score": 0.7,
            }
        ],
        paper_id=9,
        candidate_pool_size=20,
    )

    assert candidates == [
        {
            "chunk_id": 11,
            "paper_id": 9,
            "page_number": 4,
            "physical_page": 4,
            "section_title": "Results",
            "content": "Original paper body",
            "semantic_score": 0.8,
            "keyword_score": 0.4,
            "tfidf_score": 0.4,
            "score": 0.7,
            "hybrid_score": 0.7,
            "hybrid_rank": 1,
        }
    ]


def test_final_evidence_matches_direct_top_k_without_reranker(monkeypatch):
    _install_retrieval(monkeypatch, _rows(25))

    direct = rag_service.retrieve_relevant_chunks(7, "原始问题", top_k=3)
    decoupled = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
    )

    assert decoupled == direct
    assert all("hybrid_rank" not in item for item in decoupled)
    assert all("paper_id" not in item for item in decoupled)


def test_hybrid_rank_eight_survives_candidate_pool_but_not_final_top3(monkeypatch):
    _install_retrieval(monkeypatch, _rows(25))
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
        retrieval_diagnostics=diagnostics,
    )

    assert 8 in diagnostics["candidate_chunk_ids"]
    assert diagnostics["candidate_hybrid_ranks"][7] == 8
    assert 8 not in [item["chunk_id"] for item in evidence]


def test_pool_smaller_than_final_top_k_returns_entire_pool(monkeypatch):
    _install_retrieval(monkeypatch, _rows(6))
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=2,
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in evidence] == [1, 2]
    assert diagnostics["candidate_pool_size"] == 2


def test_empty_retrieval_keeps_empty_evidence_and_diagnostics(monkeypatch):
    _install_retrieval(monkeypatch, [])
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "原始问题",
        top_k=3,
        candidate_pool_size=20,
        retrieval_diagnostics=diagnostics,
    )

    assert evidence == []
    assert diagnostics["candidate_pool_limit"] == 20
    assert diagnostics["candidate_pool_size"] == 0
    assert diagnostics["candidate_chunk_ids"] == []
    assert diagnostics["candidate_hybrid_ranks"] == []


def test_candidate_pool_runs_after_existing_prf(monkeypatch):
    rows = [
        _row(1, 1.0, content="error rate evaluation"),
        _row(2, 0.9, content="classification error experiment"),
        _row(3, 0.8, content="test error result"),
        _row(4, 0.7, content="training details"),
    ]
    _install_retrieval(monkeypatch, rows)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "评价指标",
        lexical_query="evaluation metrics",
        top_k=3,
        prf_enabled=True,
        candidate_pool_size=20,
        retrieval_diagnostics=diagnostics,
    )

    assert evidence
    assert diagnostics["prf_status"] == "applied"
    assert diagnostics["candidate_pool_size"] == 4
    assert diagnostics["candidate_hybrid_ranks"] == [1, 2, 3, 4]


def test_candidate_pool_does_not_change_experimental_bm25_path(monkeypatch):
    rows = [
        _row(1, 1.0, content="evaluation metrics"),
        _row(2, 0.8, content="training procedure"),
    ]
    _install_retrieval(monkeypatch, rows)
    diagnostics = {}

    baseline = rag_service.retrieve_relevant_chunks(
        7,
        "评价指标",
        lexical_query="evaluation metrics",
        top_k=2,
        keyword_retriever=rag_service.KEYWORD_RETRIEVER_BM25,
    )
    unchanged = rag_service.retrieve_relevant_chunks(
        7,
        "评价指标",
        lexical_query="evaluation metrics",
        top_k=2,
        keyword_retriever=rag_service.KEYWORD_RETRIEVER_BM25,
        candidate_pool_size=20,
        retrieval_diagnostics=diagnostics,
    )

    assert unchanged == baseline
    assert "candidate_pool_size" not in diagnostics


def test_research_tool_passes_configured_candidate_pool_without_polluting_evidence(
    monkeypatch,
):
    captured = {}
    monkeypatch.setattr(research_tools, "RESEARCH_CANDIDATE_POOL_SIZE", 20)
    monkeypatch.setattr(research_tools, "RESEARCH_PRF_ENABLED", True)
    monkeypatch.setattr(
        research_tools,
        "get_paper",
        lambda _paper_id: {"id": 1, "title": "Paper", "page_url": ""},
    )

    def retrieve(_paper_id, _question, **kwargs):
        captured.update(kwargs)
        kwargs["retrieval_diagnostics"].update(
            {
                "candidate_pool_limit": 20,
                "candidate_pool_size": 8,
                "candidate_chunk_ids": list(range(1, 9)),
                "candidate_hybrid_ranks": list(range(1, 9)),
            }
        )
        return [{"chunk_id": 1, "page_number": 1, "content": "evidence"}]

    monkeypatch.setattr(research_tools, "retrieve_relevant_chunks", retrieve)

    result = research_tools.retrieve_paper_evidence_with_lexical_query(
        1,
        "原问题",
        lexical_query="english query",
        top_k=3,
    )

    assert captured["candidate_pool_size"] == 20
    assert result["input"]["candidate_pool_size"] == 20
    assert result["diagnostics"]["candidate_pool_size"] == 8
    assert "candidate_pool_size" not in result["evidence"][0]
    assert "hybrid_rank" not in result["evidence"][0]


def test_candidate_pool_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="candidate_pool_size must be positive"):
        rag_service._build_candidate_pool([], paper_id=1, candidate_pool_size=0)


@pytest.mark.parametrize("raw_value", ["0", "-1", "invalid"])
def test_candidate_pool_config_rejects_invalid_values(raw_value):
    with pytest.raises(ValueError, match="RESEARCH_CANDIDATE_POOL_SIZE"):
        config._positive_int_setting(
            {"RESEARCH_CANDIDATE_POOL_SIZE": raw_value},
            "RESEARCH_CANDIDATE_POOL_SIZE",
            20,
        )

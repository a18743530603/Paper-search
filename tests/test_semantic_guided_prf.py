import json
import math

import pytest

from my_agent_project import rag_service, research_tools


def _row(chunk_id, content, dense, *, section="Experiments"):
    return {
        "id": chunk_id,
        "page_number": chunk_id,
        "chunk_index": chunk_id - 1,
        "content": content,
        "vector_json": json.dumps(rag_service.term_frequency(content)),
        "dense_vector_json": json.dumps(dense),
        "parent_chunk_id": None,
        "parent_content": None,
        "parent_page_number": None,
        "parent_index": None,
        "section_title": section,
        "content_type": "text",
        "bbox_json": None,
        "chunk_role": "retrieval",
    }


def _install_retrieval(monkeypatch, rows_by_paper):
    monkeypatch.setattr(rag_service, "get_document", lambda _paper_id: None)
    monkeypatch.setattr(
        rag_service,
        "list_indexed_chunks",
        lambda paper_id: rows_by_paper[paper_id],
    )
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)
    monkeypatch.setattr(
        rag_service,
        "_cached_seed_embeddings",
        lambda texts, **_kwargs: ([[1.0, 0.0] for _text in texts], "seed-test"),
    )


def test_prf_disabled_preserves_current_tfidf_results(monkeypatch):
    rows = [
        _row(1, "evaluation metrics accuracy", [1.0, 0.0]),
        _row(2, "training procedure", [0.0, 1.0]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    diagnostics = {}

    baseline = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
        top_k=2,
    )
    disabled = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
        top_k=2,
        prf_enabled=False,
        retrieval_diagnostics=diagnostics,
    )

    assert disabled == baseline
    assert diagnostics["prf_enabled"] is False
    assert diagnostics["prf_status"] == "disabled"


def test_prf_feedback_uses_pure_semantic_top3_not_hybrid_top3(monkeypatch):
    rows = [
        _row(1, "alpha method", [1.0, 0.0]),
        _row(2, "beta procedure", [0.4, math.sqrt(0.84)]),
        _row(3, "gamma approach", [0.1, math.sqrt(0.99)]),
        _row(4, "metrics metrics exact", [0.0, 1.0]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    diagnostics = {}

    baseline = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        top_k=4,
    )
    rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        top_k=4,
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert [item["chunk_id"] for item in baseline[:3]] == [1, 2, 4]
    assert diagnostics["feedback_chunk_ids"] == [1, 2, 3]
    assert diagnostics["feedback_chunk_count"] == 3
    assert diagnostics["prf_status"] == "applied"


def test_prf_feedback_is_isolated_per_paper(monkeypatch):
    rows_a = [
        _row(1, "alphaunique method metrics", [1.0, 0.0]),
        _row(2, "alphaunique experiment", [0.9, 0.1]),
        _row(3, "alphaunique result", [0.8, 0.2]),
    ]
    rows_b = [
        _row(11, "betaunique method metrics", [1.0, 0.0]),
        _row(12, "betaunique experiment", [0.9, 0.1]),
        _row(13, "betaunique result", [0.8, 0.2]),
    ]
    _install_retrieval(monkeypatch, {1: rows_a, 2: rows_b})
    diagnostics_a = {}
    diagnostics_b = {}

    rag_service.retrieve_relevant_chunks(
        1,
        "方法",
        lexical_query="metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics_a,
    )
    rag_service.retrieve_relevant_chunks(
        2,
        "方法",
        lexical_query="metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics_b,
    )

    assert "alphaunique" in diagnostics_a["feedback_terms"]
    assert "betaunique" not in diagnostics_a["feedback_terms"]
    assert "betaunique" in diagnostics_b["feedback_terms"]
    assert "alphaunique" not in diagnostics_b["feedback_terms"]


def test_prf_extracts_terms_from_body_only(monkeypatch):
    rows = [
        _row(
            1,
            "[Section: metadataonly]\nbodyterm metrics evidence",
            [1.0, 0.0],
            section="sectiononly",
        ),
        _row(2, "bodyterm experiment metrics", [0.9, 0.1], section="sectiononly"),
        _row(3, "bodyterm result metrics", [0.8, 0.2], section="sectiononly"),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    diagnostics = {}

    rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert "bodyterm" in diagnostics["feedback_terms"]
    assert "metadataonly" not in diagnostics["feedback_terms"]
    assert "sectiononly" not in diagnostics["feedback_terms"]


def test_prf_feedback_terms_are_capped_at_ten(monkeypatch):
    terms = " ".join(f"term{index:02d}" for index in range(15))
    rows = [
        _row(1, f"metrics {terms}", [1.0, 0.0]),
        _row(2, f"metrics {terms}", [0.9, 0.1]),
        _row(3, f"metrics {terms}", [0.8, 0.2]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    diagnostics = {}

    rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert len(diagnostics["feedback_terms"]) == 10
    assert diagnostics["expansion_term_count"] == 10
    assert len(diagnostics["feedback_term_scores"]) == 10


def test_prf_vector_interpolation_normalizes_both_branches():
    enhanced = rag_service._build_enhanced_tfidf_query(
        {"query": 3.0, "shared": 4.0},
        [("feedback", 12.0), ("shared", 5.0)],
    )

    assert enhanced["query"] == pytest.approx(0.5 * 3 / 5)
    assert enhanced["feedback"] == pytest.approx(0.5 * 12 / 13)
    assert enhanced["shared"] == pytest.approx(0.5 * 4 / 5 + 0.5 * 5 / 13)
    assert enhanced["query"] > 0
    assert enhanced["feedback"] > 0


def test_prf_falls_back_to_original_tfidf_when_no_terms(monkeypatch):
    rows = [
        _row(1, "the and of", [1.0, 0.0]),
        _row(2, "the and of", [0.9, 0.1]),
        _row(3, "the and of", [0.8, 0.2]),
        _row(4, "metrics", [0.0, 1.0]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    diagnostics = {}

    baseline = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        top_k=4,
    )
    fallback = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        top_k=4,
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert fallback == baseline
    assert diagnostics["prf_status"] == "fallback_no_terms"
    assert diagnostics["feedback_terms"] == []


def test_prf_falls_back_when_semantic_feedback_is_unavailable(monkeypatch):
    rows = [
        _row(1, "evaluation metrics", [1.0, 0.0]),
        _row(2, "training method", [0.0, 1.0]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    diagnostics = {}

    baseline = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
    )
    fallback = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert fallback == baseline
    assert diagnostics["prf_status"] == "fallback_no_feedback_chunks"
    assert diagnostics["feedback_chunk_ids"] == []


def test_prf_runtime_error_is_recorded_and_retrieval_continues(monkeypatch):
    rows = [
        _row(1, "evaluation metrics", [1.0, 0.0]),
        _row(2, "classification error", [0.9, 0.1]),
        _row(3, "test error", [0.8, 0.2]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    monkeypatch.setattr(
        rag_service,
        "_extract_prf_feedback_terms",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("prf failed")),
    )
    diagnostics = {}

    baseline = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
    )
    fallback = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert fallback == baseline
    assert diagnostics["prf_status"] == "fallback_error"
    assert "prf failed" in diagnostics["error"]


def test_prf_does_not_call_llm_and_returns_diagnostics(monkeypatch):
    rows = [
        _row(1, "error rate metrics", [1.0, 0.0]),
        _row(2, "classification error", [0.9, 0.1]),
        _row(3, "test error", [0.8, 0.2]),
    ]
    _install_retrieval(monkeypatch, {1: rows})
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PRF must not call ask_deepseek")
        ),
    )
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PRF must not call ask_deepseek_json")
        ),
    )
    diagnostics = {}

    results = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="metrics",
        prf_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert results
    assert diagnostics == {
        "prf_enabled": True,
        "prf_status": "applied",
        "feedback_chunk_ids": [1, 2, 3],
        "feedback_terms": diagnostics["feedback_terms"],
        "feedback_term_scores": diagnostics["feedback_term_scores"],
        "feedback_chunk_count": 3,
        "expansion_term_count": len(diagnostics["feedback_terms"]),
        "original_query_weight": 0.5,
        "feedback_weight": 0.5,
        "error": "",
    }
    assert "error" in diagnostics["feedback_terms"]


@pytest.mark.parametrize("enabled", [False, True])
def test_routed_research_tool_passes_prf_flag_and_keeps_diagnostics(
    monkeypatch,
    enabled,
):
    captured = {}
    monkeypatch.setattr(research_tools, "RESEARCH_PRF_ENABLED", enabled)
    monkeypatch.setattr(
        research_tools,
        "get_paper",
        lambda _paper_id: {"id": 1, "title": "Paper", "page_url": ""},
    )

    def retrieve(_paper_id, _question, **kwargs):
        captured.update(kwargs)
        kwargs["retrieval_diagnostics"].update(
            {
                "prf_enabled": enabled,
                "prf_status": "applied" if enabled else "disabled",
            }
        )
        return [{"chunk_id": 1, "page_number": 1, "content": "evidence"}]

    monkeypatch.setattr(research_tools, "retrieve_relevant_chunks", retrieve)

    result = research_tools.retrieve_paper_evidence_with_lexical_query(
        1,
        "原问题",
        lexical_query="english query",
    )

    assert captured["prf_enabled"] is enabled
    assert result["diagnostics"]["prf_enabled"] is enabled
    assert result["diagnostics"]["prf_status"] == (
        "applied" if enabled else "disabled"
    )
    assert "diagnostics" not in result["evidence"][0]
    assert "feedback_terms" not in result["evidence"][0]

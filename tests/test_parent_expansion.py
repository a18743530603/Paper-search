import json

import pytest

from my_agent_project import (
    parent_expansion_service,
    rag_service,
    research_tools,
)


def _seed(
    chunk_id: int,
    mmr_rank: int,
    *,
    parent_id: int | None,
    paper_id: int = 7,
    content: str | None = None,
):
    return {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "parent_chunk_id": parent_id,
        "page_number": chunk_id,
        "chunk_index": chunk_id - 1,
        "content": content or f"small child {chunk_id}",
        "score": 1.0 - chunk_id * 0.01,
        "hybrid_rank": mmr_rank,
        "rerank_score": 1.0 - chunk_id * 0.01,
        "rerank_rank": mmr_rank,
        "mmr_score": 1.0 - chunk_id * 0.01,
        "mmr_rank": mmr_rank,
    }


def _parent(parent_id: int, *, paper_id: int = 7, page: int = 9, content=None):
    return {
        "id": parent_id,
        "paper_id": paper_id,
        "page_number": page,
        "parent_index": parent_id - 1,
        "content": content or f"complete parent context {parent_id}",
    }


def _row(chunk_id: int, parent_id: int | None, embedding):
    child_content = f"metric child evidence {chunk_id}"
    return {
        "id": chunk_id,
        "paper_id": 7,
        "page_number": chunk_id,
        "chunk_index": chunk_id - 1,
        "content": child_content,
        "vector_json": json.dumps(rag_service.term_frequency(child_content)),
        "dense_vector_json": json.dumps(embedding),
        "parent_chunk_id": parent_id,
        "parent_content": f"PARENT MUST NOT RERANK {parent_id}",
        "parent_page_number": 40 + chunk_id,
        "parent_index": 100 + chunk_id,
        "section_title": "Experiments",
        "content_type": "text",
        "bbox_json": None,
        "chunk_role": "retrieval",
    }


def _install_retrieval(monkeypatch):
    rows = [
        _row(1, 101, [1.0, 0.0, 0.0]),
        _row(2, 102, [0.9, 0.1, 0.0]),
        _row(3, 103, [0.1, 0.9, 0.0]),
        _row(4, 104, [0.0, 0.1, 0.9]),
    ]
    calls = {"embedding": 0, "reranker": 0}
    monkeypatch.setattr(rag_service, "get_document", lambda _paper_id: None)
    monkeypatch.setattr(rag_service, "list_indexed_chunks", lambda _paper_id: rows)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)

    def embed(texts, **_kwargs):
        calls["embedding"] += 1
        return [[1.0, 0.0, 0.0] for _text in texts], "seed-test"

    def rerank(_query, candidates, **_kwargs):
        calls["reranker"] += 1
        reranked = []
        for rank, candidate in enumerate(candidates, start=1):
            item = dict(candidate)
            item["rerank_score"] = float(len(candidates) - rank + 1)
            item["rerank_rank"] = rank
            reranked.append(item)
        return reranked

    monkeypatch.setattr(rag_service, "_cached_seed_embeddings", embed)
    monkeypatch.setattr(rag_service, "rerank_candidates", rerank)
    return rows, calls


def test_basic_seed_expands_to_original_parent_content():
    seed = _seed(10, 1, parent_id=100)
    seed["section_title"] = "Discussion"
    seed["section_path"] = "Paper A > Discussion"
    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        [seed],
        paper_id=7,
        parent_lookup=lambda _paper_id, _ids: {
            100: _parent(100, page=8, content="full original parent")
        },
    )

    assert expanded[0]["content"] == "full original parent"
    assert expanded[0]["page_number"] == 8
    assert expanded[0]["chunk_id"] == 10
    assert expanded[0]["parent_chunk_id"] == 100
    assert expanded[0]["seed_chunk_ids"] == [10]
    assert expanded[0]["section_title"] == "Discussion"
    assert expanded[0]["section_path"] == "Paper A > Discussion"
    assert diagnostics["parent_expansion_status"] == "applied"


def test_missing_parent_relation_keeps_seed_chunk():
    seed = _seed(10, 1, parent_id=None)
    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        [seed],
        paper_id=7,
        parent_lookup=lambda _paper_id, _ids: {},
    )

    assert expanded[0]["content"] == seed["content"]
    assert diagnostics["parent_fallback_chunk_ids"] == [10]
    assert diagnostics["parent_expansion_details"][0]["action"] == (
        "fallback_no_parent"
    )


def test_missing_parent_row_keeps_seed_chunk():
    seed = _seed(10, 1, parent_id=100)
    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        [seed],
        paper_id=7,
        parent_lookup=lambda _paper_id, _ids: {},
    )

    assert expanded[0]["content"] == seed["content"]
    assert diagnostics["parent_expansion_details"][0]["action"] == (
        "fallback_missing_parent"
    )


def test_same_parent_is_deduplicated_by_first_mmr_seed():
    seeds = [
        _seed(10, 1, parent_id=100),
        _seed(11, 2, parent_id=100),
        _seed(12, 3, parent_id=200),
    ]
    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        seeds,
        paper_id=7,
        parent_lookup=lambda _paper_id, _ids: {
            100: _parent(100),
            200: _parent(200),
        },
    )

    assert [item["parent_chunk_id"] for item in expanded] == [100, 200]
    assert expanded[0]["chunk_id"] == 10
    assert expanded[0]["seed_chunk_ids"] == [10, 11]
    assert expanded[0]["seed_mmr_rank"] == 1
    assert diagnostics["parent_dedup_count"] == 1


def test_parent_expansion_preserves_mmr_order_not_parent_id_order():
    seeds = [
        _seed(10, 1, parent_id=30),
        _seed(11, 2, parent_id=10),
    ]
    expanded, _diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        seeds,
        paper_id=7,
        parent_lookup=lambda _paper_id, _ids: {
            10: _parent(10),
            30: _parent(30),
        },
    )

    assert [item["parent_chunk_id"] for item in expanded] == [30, 10]


def test_cross_paper_parent_is_rejected_and_child_page_is_preserved():
    seed = _seed(10, 1, parent_id=100)
    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        [seed],
        paper_id=7,
        parent_lookup=lambda _paper_id, _ids: {100: _parent(100, paper_id=8)},
    )

    assert expanded[0]["content"] == seed["content"]
    assert expanded[0]["page_number"] == 10
    assert diagnostics["parent_expansion_details"][0]["action"] == (
        "fallback_cross_paper"
    )


def test_oversized_parent_falls_back_to_small_seed():
    seed = _seed(10, 1, parent_id=100)
    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        [seed],
        paper_id=7,
        max_estimated_tokens=5,
        parent_lookup=lambda _paper_id, _ids: {
            100: _parent(100, content="x" * 100)
        },
    )

    assert expanded[0]["content"] == seed["content"]
    assert diagnostics["parent_expansion_details"][0]["action"] == (
        "fallback_oversized"
    )


def test_parent_lookup_failure_falls_back_without_losing_any_seed():
    seeds = [_seed(10, 1, parent_id=100), _seed(11, 2, parent_id=200)]

    def fail_lookup(_paper_id, _ids):
        raise RuntimeError("database unavailable")

    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        seeds,
        paper_id=7,
        parent_lookup=fail_lookup,
    )

    assert [item["chunk_id"] for item in expanded] == [10, 11]
    assert diagnostics["parent_expansion_status"] == "fallback_lookup_error"
    assert "database unavailable" in diagnostics["parent_expansion_error"]


def test_disabled_parent_expansion_exactly_preserves_mmr_v1(monkeypatch):
    _rows, _calls = _install_retrieval(monkeypatch)
    monkeypatch.setattr(
        parent_expansion_service,
        "get_paper_parent_chunks",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("disabled parent expansion must not query parents")
        ),
    )
    baseline = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=2,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
    )
    diagnostics = {}

    disabled = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=2,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        parent_expansion_enabled=False,
        retrieval_diagnostics=diagnostics,
    )

    assert disabled == baseline
    assert diagnostics["parent_expansion_status"] == "disabled"


def test_parent_expansion_runs_after_reranker_and_mmr_on_small_chunks(monkeypatch):
    _rows, calls = _install_retrieval(monkeypatch)
    events = []
    original_mmr = rag_service.select_mmr_candidates

    def rerank(_query, candidates, **_kwargs):
        events.append("reranker")
        assert all(item["content"].startswith("metric child") for item in candidates)
        return [
            {**item, "rerank_score": 5.0 - rank, "rerank_rank": rank}
            for rank, item in enumerate(candidates, start=1)
        ]

    def select_mmr(candidates, **kwargs):
        events.append("mmr")
        assert all(item["content"].startswith("metric child") for item in candidates)
        return original_mmr(candidates, **kwargs)

    def lookup(_paper_id, parent_ids):
        events.append("parent_lookup")
        return {
            parent_id: _parent(
                parent_id,
                page=parent_id - 90,
                content=f"expanded parent {parent_id}",
            )
            for parent_id in parent_ids
        }

    monkeypatch.setattr(rag_service, "rerank_candidates", rerank)
    monkeypatch.setattr(rag_service, "select_mmr_candidates", select_mmr)
    monkeypatch.setattr(parent_expansion_service, "get_paper_parent_chunks", lookup)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=2,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        parent_expansion_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert events == ["reranker", "mmr", "parent_lookup"]
    assert all(item["content"].startswith("expanded parent") for item in evidence)
    assert diagnostics["parent_expansion_status"] == "applied"
    assert diagnostics["seed_chunk_ids"] == diagnostics["mmr_selected_chunk_ids"]
    assert calls["embedding"] == 1


def test_parent_expansion_receives_final_five_seed_chunks(monkeypatch):
    rows, _calls = _install_retrieval(monkeypatch)
    rows.extend(
        [
            _row(5, 105, [0.2, 0.2, 0.8]),
            _row(6, 106, [0.2, 0.8, 0.2]),
        ]
    )
    parent_expansion_calls = []

    def lookup(_paper_id, parent_ids):
        parent_expansion_calls.append(list(parent_ids))
        return {
            parent_id: _parent(parent_id, page=parent_id - 90)
            for parent_id in parent_ids
        }

    monkeypatch.setattr(parent_expansion_service, "get_paper_parent_chunks", lookup)
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=5,
        candidate_pool_size=50,
        reranker_enabled=True,
        mmr_enabled=True,
        parent_expansion_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert len(evidence) == 5
    assert len(parent_expansion_calls) == 1
    assert len(parent_expansion_calls[0]) == 5
    assert diagnostics["seed_chunk_ids"] == diagnostics["final_seed_chunk_ids"]
    assert diagnostics["final_seed_count"] == 5


def test_expanded_evidence_keeps_real_page_and_cleans_internal_fields(monkeypatch):
    _rows, _calls = _install_retrieval(monkeypatch)
    monkeypatch.setattr(
        parent_expansion_service,
        "get_paper_parent_chunks",
        lambda _paper_id, parent_ids: {
            parent_id: _parent(parent_id, page=15) for parent_id in parent_ids
        },
    )
    diagnostics = {}

    evidence = rag_service.retrieve_relevant_chunks(
        7,
        "metric",
        top_k=2,
        candidate_pool_size=20,
        reranker_enabled=True,
        mmr_enabled=True,
        parent_expansion_enabled=True,
        retrieval_diagnostics=diagnostics,
    )

    assert all(item["page_number"] == 15 for item in evidence)
    assert all(item["parent_chunk_id"] for item in evidence)
    assert all(item["seed_chunk_ids"] for item in evidence)
    forbidden = {
        "hybrid_rank",
        "rerank_rank",
        "rerank_score",
        "mmr_rank",
        "mmr_score",
        "mmr_relevance",
        "mmr_max_similarity",
        "_mmr_embedding",
        "parent_expansion_action",
    }
    assert all(not forbidden.intersection(item) for item in evidence)
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "expanded parent" not in serialized
    assert "metric child" not in serialized
    assert "_mmr_embedding" not in serialized


def test_research_tool_passes_parent_expansion_configuration(monkeypatch):
    captured = {}
    monkeypatch.setattr(research_tools, "RESEARCH_PARENT_EXPANSION_ENABLED", True)
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

    assert captured["parent_expansion_enabled"] is True
    assert result["input"]["parent_expansion_enabled"] is True

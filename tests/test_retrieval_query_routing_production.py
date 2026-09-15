import json

from my_agent_project import (
    rag_service,
    research_conversation_service,
    research_evidence_service,
    retrieval_query_service,
)


def _route_with_storage(monkeypatch, responses):
    storage = {}
    calls = []

    monkeypatch.setattr(
        retrieval_query_service,
        "get_cached_llm_response",
        lambda namespace, input_hash: storage.get((namespace, input_hash)),
    )
    monkeypatch.setattr(
        retrieval_query_service,
        "upsert_cached_llm_response",
        lambda namespace, input_hash, response: storage.__setitem__(
            (namespace, input_hash), response
        ),
    )

    def rewrite(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0), "deepseek-test"

    monkeypatch.setattr(retrieval_query_service, "ask_deepseek_json", rewrite)
    return storage, calls


def test_query_rewrite_generates_one_english_query_and_reuses_cache(monkeypatch):
    storage, calls = _route_with_storage(
        monkeypatch,
        [{"query": "evaluation metrics used in the experiments"}],
    )

    first = retrieval_query_service.route_retrieval_query(
        "这篇论文使用了什么评价指标？",
        enabled=True,
    )
    second = retrieval_query_service.route_retrieval_query(
        "这篇论文使用了什么评价指标？",
        enabled=True,
    )

    assert first.original_query == "这篇论文使用了什么评价指标？"
    assert first.lexical_query == "evaluation metrics used in the experiments"
    assert first.query_rewrite_status == "generated"
    assert second.query_rewrite_status == "cache_hit"
    assert second.lexical_query == first.lexical_query
    assert len(calls) == 1
    assert len(storage) == 1
    system_prompt = calls[0][0][0]
    user_payload = json.loads(calls[0][0][1])
    assert "Do not answer" in system_prompt
    assert user_payload["original_query"] == "这篇论文使用了什么评价指标？"


def test_query_rewrite_failure_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(
        retrieval_query_service,
        "get_cached_llm_response",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        retrieval_query_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
    )

    routed = retrieval_query_service.route_retrieval_query(
        "这篇论文使用了什么评价指标？",
        enabled=True,
    )

    assert routed.lexical_query == routed.original_query
    assert routed.query_rewrite_status == "fallback"
    assert routed.tfidf_query_type == "original_query_fallback"
    assert "timeout" in routed.error


def test_disabled_query_routing_keeps_old_behavior_without_model_or_cache(monkeypatch):
    monkeypatch.setattr(
        retrieval_query_service,
        "get_cached_llm_response",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cache must not run")),
    )
    monkeypatch.setattr(
        retrieval_query_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not run")
        ),
    )

    routed = retrieval_query_service.route_retrieval_query(
        "原始问题",
        enabled=False,
    )

    assert routed.lexical_query == "原始问题"
    assert routed.query_rewrite_status == "disabled"
    assert routed.semantic_query_type == "original_query"
    assert routed.tfidf_query_type == "original_query"


def test_config_switch_false_keeps_old_behavior(monkeypatch):
    monkeypatch.setattr(
        retrieval_query_service,
        "RESEARCH_QUERY_ROUTING_ENABLED",
        False,
    )
    monkeypatch.setattr(
        retrieval_query_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not run")
        ),
    )

    routed = retrieval_query_service.route_retrieval_query("原始问题")

    assert routed.query_rewrite_status == "disabled"
    assert routed.lexical_query == "原始问题"


def test_malformed_query_rewrite_output_falls_back(monkeypatch):
    monkeypatch.setattr(
        retrieval_query_service,
        "get_cached_llm_response",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        retrieval_query_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: ({"query": ""}, "deepseek-test"),
    )

    routed = retrieval_query_service.route_retrieval_query("评价指标", enabled=True)

    assert routed.query_rewrite_status == "fallback"
    assert routed.lexical_query == "评价指标"


def test_retrieval_routes_original_to_semantic_and_english_to_tfidf(monkeypatch):
    rows = [
        {
            "id": 1,
            "page_number": 4,
            "chunk_index": 0,
            "content": "evaluation metrics",
            "vector_json": json.dumps({"evaluation": 0.5, "metrics": 0.5}),
            "dense_vector_json": json.dumps([1.0, 0.0]),
            "parent_chunk_id": None,
            "parent_content": None,
            "parent_page_number": None,
            "parent_index": None,
            "section_title": "Experiments",
            "content_type": "text",
            "bbox_json": None,
            "chunk_role": "retrieval",
        },
        {
            "id": 2,
            "page_number": 5,
            "chunk_index": 1,
            "content": "training procedure",
            "vector_json": json.dumps({"training": 0.5, "procedure": 0.5}),
            "dense_vector_json": json.dumps([0.0, 1.0]),
            "parent_chunk_id": None,
            "parent_content": None,
            "parent_page_number": None,
            "parent_index": None,
            "section_title": "Training",
            "content_type": "text",
            "bbox_json": None,
            "chunk_role": "retrieval",
        },
    ]
    embedded = []
    monkeypatch.setattr(rag_service, "get_document", lambda _paper_id: None)
    monkeypatch.setattr(rag_service, "list_indexed_chunks", lambda _paper_id: rows)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)

    def embed(texts, **_kwargs):
        embedded.extend(texts)
        return [[1.0, 0.0]], "seed-test"

    monkeypatch.setattr(rag_service, "_cached_seed_embeddings", embed)

    baseline = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        top_k=2,
    )
    routed = rag_service.retrieve_relevant_chunks(
        1,
        "评价指标",
        lexical_query="evaluation metrics",
        top_k=2,
    )
    baseline_by_id = {item["chunk_id"]: item for item in baseline}
    routed_by_id = {item["chunk_id"]: item for item in routed}

    assert embedded == ["评价指标", "评价指标"]
    assert baseline_by_id[1]["semantic_score"] == routed_by_id[1]["semantic_score"]
    assert baseline_by_id[1]["keyword_score"] == 0.0
    assert routed_by_id[1]["keyword_score"] > 0.0
    assert routed_by_id[1]["score"] > baseline_by_id[1]["score"]


def test_followup_routes_once_for_all_papers_without_polluting_prompt(monkeypatch):
    original = "这篇论文使用了什么评价指标？"
    lexical = "evaluation metrics used in the experiments"
    route_calls = []
    retrieval_calls = []

    monkeypatch.setattr(
        research_conversation_service,
        "get_research_task",
        lambda _task_id: {
            "status": "papers_selected",
            "selected_paper_ids": [11, 12],
        },
    )
    monkeypatch.setattr(
        research_conversation_service,
        "get_research_report",
        lambda _task_id: None,
    )
    monkeypatch.setattr(
        research_conversation_service,
        "update_task_memory_from_message",
        lambda *_args, **_kwargs: {"memories": [], "error": ""},
    )
    monkeypatch.setattr(
        research_conversation_service,
        "prepare_research_conversation_context",
        lambda *_args, **_kwargs: {
            "conversation_summary": "",
            "recent_messages": [],
            "summary_error": "",
        },
    )

    def route(question):
        route_calls.append(question)
        return retrieval_query_service.RetrievalQueryRoute(
            original_query=question,
            lexical_query=lexical,
            query_routing_enabled=True,
            query_rewrite_status="generated",
            semantic_query_type="original_query",
            tfidf_query_type="english_retrieval_query",
            model="deepseek-test",
        )

    monkeypatch.setattr(research_evidence_service, "route_retrieval_query", route)

    def retrieve(paper_id, question, **kwargs):
        retrieval_calls.append((paper_id, question, kwargs))
        return {
            "tool_call": {
                "input": {
                    "question": question,
                    "lexical_query": kwargs["lexical_query"],
                }
            },
            "evidence": [
                {
                    "paper_id": paper_id,
                    "paper_title": f"Paper {paper_id}",
                    "page_number": 4,
                    "chunk_id": paper_id,
                    "score": 0.9,
                    "content": "The experiments use accuracy.",
                }
            ],
            "access_limitation": None,
        }

    monkeypatch.setattr(
        research_evidence_service,
        "retrieve_report_paper_evidence",
        retrieve,
    )
    monkeypatch.setattr(
        research_conversation_service,
        "run_coverage_research_loop",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("force legacy evidence fallback")
        ),
    )

    context = research_conversation_service.prepare_research_followup_context(
        7,
        original,
    )
    prompt = research_conversation_service.build_research_followup_prompt(
        original,
        context["report_content"],
        context["evidence"],
        context["access_limitations"],
    )

    assert route_calls == [original]
    assert retrieval_calls == [
        (11, original, {"lexical_query": lexical, "top_k": 5}),
        (12, original, {"lexical_query": lexical, "top_k": 5}),
    ]
    assert context["query_routing"]["original_query"] == original
    assert context["query_routing"]["lexical_query"] == lexical
    assert context["retrieval_tool_calls"][0]["input"]["question"] == original
    assert context["retrieval_tool_calls"][0]["input"]["lexical_query"] == lexical
    assert lexical not in prompt
    assert original in prompt
    assert all("lexical_query" not in item for item in context["evidence"])

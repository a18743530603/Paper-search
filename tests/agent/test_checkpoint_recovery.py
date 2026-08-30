import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from my_agent_project import agent_trace_service, db, research_agent, research_tools
from my_agent_project.paper_search_mcp_service import McpSearchBatch
from my_agent_project.schemas import PaperCandidate, STATUS_AVAILABLE


def _config(thread_id: str) -> dict:
    return {
        "configurable": {
            "thread_id": thread_id,
            agent_trace_service.TRACE_ENABLED_KEY: True,
        }
    }


def _graph(checkpoint_path: Path):
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    graph = research_agent.build_checkpointed_research_graph(
        SqliteSaver(connection)
    )
    return connection, graph


def _planned_response(*_args, **_kwargs):
    return (
        {
            "goal": "Study agent memory",
            "queries": ["agent memory architecture", "agent memory retrieval"],
            "rationale": "Cover storage and retrieval.",
        },
        "deepseek-test",
    )


def _paper() -> PaperCandidate:
    return PaperCandidate(
        title="Agent Memory Systems",
        authors="Test Author",
        summary="A controlled checkpoint test paper.",
        published="2026-01-01",
        source="arxiv",
        publisher="arXiv",
        doi=None,
        page_url="https://example.org/paper",
        pdf_url="https://example.org/paper.pdf",
        status=STATUS_AVAILABLE,
    )


def _batch_search(search_one):
    def search(queries, *, sources, max_results):
        batches = []
        for query in queries:
            papers = search_one(query, max_results)
            batches.append(
                McpSearchBatch(
                    query=query,
                    candidates=papers,
                    source_counts={},
                    errors={},
                    raw_total=len(papers),
                )
            )
        return batches

    return search


def _node_traces(thread_id: str, node_name: str) -> list[dict]:
    return [
        trace
        for trace in db.list_agent_traces(thread_id=thread_id)
        if trace["node_name"] == node_name
    ]


def test_plan_confirmation_recovers_with_trace_and_without_replanning(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    planner_calls = 0
    search_calls = 0

    def plan(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return _planned_response(*args, **kwargs)

    def search(*_args, **_kwargs):
        nonlocal search_calls
        search_calls += 1
        return [_paper()]

    monkeypatch.setattr(research_agent, "ask_deepseek_json", plan)
    monkeypatch.setattr(research_tools, "search_many", _batch_search(search))
    checkpoint_path = tmp_path / "plan-confirmation.db"
    thread_id = "checkpoint-plan-confirmation"
    config = _config(thread_id)

    first_connection, first_graph = _graph(checkpoint_path)
    waiting = first_graph.invoke(
        {"question": "How is memory stored?", "max_results": 5},
        config=config,
    )
    assert "__interrupt__" in waiting
    first_connection.close()

    resumed_connection, resumed_graph = _graph(checkpoint_path)
    resumed = resumed_graph.invoke(
        Command(resume={"approved": False}),
        config=config,
    )

    assert resumed["workflow_status"] == "cancelled"
    assert planner_calls == 1
    assert search_calls == 0
    assert [trace["status"] for trace in _node_traces(thread_id, "plan_research")] == [
        "success"
    ]
    assert [
        trace["status"]
        for trace in _node_traces(thread_id, "request_plan_confirmation")
    ] == ["interrupted", "success"]
    resumed_connection.close()


def test_plan_can_be_revised_at_same_confirmation_checkpoint(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    search_queries: list[str] = []
    monkeypatch.setattr(research_agent, "ask_deepseek_json", _planned_response)
    monkeypatch.setattr(
        research_tools,
        "search_many",
        _batch_search(
            lambda query, _max_results: search_queries.append(query) or [_paper()]
        ),
    )
    connection, graph = _graph(tmp_path / "revised-plan.db")
    config = _config("checkpoint-revised-plan")
    graph.invoke(
        {"question": "RAG", "max_results": 5},
        config=config,
    )

    graph.update_state(
        config,
        {
            "question": "Compare RAG faithfulness and retrieval recall",
            "plan_goal": "Compare two RAG evaluation dimensions",
            "plan_queries": [
                "RAG faithfulness evaluation",
                "RAG retrieval recall evaluation",
            ],
            "plan_rationale": "Cover generation and retrieval quality.",
            "plan_model": "deepseek-test",
            "plan_status": "planned",
            "plan_error": "",
        },
    )
    graph.invoke(Command(resume={"approved": True}), config=config)
    snapshot = graph.get_state(config)

    assert snapshot.values["question"] == (
        "Compare RAG faithfulness and retrieval recall"
    )
    assert search_queries == [
        "RAG faithfulness evaluation",
        "RAG retrieval recall evaluation",
    ]
    assert snapshot.next == ("request_paper_selection",)
    connection.close()


def test_checkpoint_resume_keeps_sources_and_batches_queries_once(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    planner_calls = 0
    batch_calls = []

    def plan(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return _planned_response(*args, **kwargs)

    def search_many(queries, *, sources, max_results):
        batch_calls.append(
            {
                "queries": tuple(queries),
                "sources": tuple(sources),
                "max_results": max_results,
            }
        )
        return [
            McpSearchBatch(
                query=query,
                candidates=[_paper()],
                source_counts={"openalex": 1},
                errors={},
                raw_total=1,
            )
            for query in queries
        ]

    monkeypatch.setattr(research_agent, "ask_deepseek_json", plan)
    monkeypatch.setattr(research_tools, "search_many", search_many)
    checkpoint_path = tmp_path / "source-resume.db"
    config = _config("checkpoint-source-resume")

    first_connection, first_graph = _graph(checkpoint_path)
    first_graph.invoke(
        {
            "question": "How is memory stored?",
            "max_results": 6,
            "sources": ["semantic", "openalex"],
        },
        config=config,
    )
    assert first_graph.get_state(config).values["sources"] == [
        "semantic",
        "openalex",
    ]
    first_graph.update_state(
        config,
        {"sources": ["semantic", "openalex"]},
    )
    first_connection.close()

    resumed_connection, resumed_graph = _graph(checkpoint_path)
    resumed_graph.invoke(Command(resume={"approved": True}), config=config)
    snapshot = resumed_graph.get_state(config)

    assert planner_calls == 1
    assert batch_calls == [
        {
            "queries": ("agent memory architecture", "agent memory retrieval"),
            "sources": ("semantic", "openalex"),
            "max_results": 3,
        }
    ]
    assert snapshot.values["sources"] == ["semantic", "openalex"]
    assert snapshot.next == ("request_paper_selection",)
    resumed_connection.close()


def test_paper_selection_recovers_without_repeating_plan_or_search(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    planner_calls = 0
    search_queries: list[str] = []

    def plan(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return _planned_response(*args, **kwargs)

    monkeypatch.setattr(research_agent, "ask_deepseek_json", plan)
    monkeypatch.setattr(
        research_tools,
        "search_many",
        _batch_search(
            lambda query, _max_results: search_queries.append(query) or [_paper()]
        ),
    )
    checkpoint_path = tmp_path / "paper-selection.db"
    thread_id = "checkpoint-paper-selection"
    config = _config(thread_id)

    first_connection, first_graph = _graph(checkpoint_path)
    first_graph.invoke(
        {"question": "How is memory stored?", "max_results": 5},
        config=config,
    )
    selection_waiting = first_graph.invoke(
        Command(resume={"approved": True}),
        config=config,
    )
    assert "__interrupt__" in selection_waiting
    first_graph.update_state(config, {"candidate_paper_ids": [101]})
    first_connection.close()

    resumed_connection, resumed_graph = _graph(checkpoint_path)
    selected = resumed_graph.invoke(
        Command(resume={"paper_ids": [101], "cancelled": False}),
        config=config,
    )

    assert "__interrupt__" not in selected
    assert selected["workflow_status"] == "papers_selected"
    assert resumed_graph.get_state(config).next == ()
    assert planner_calls == 1
    assert search_queries == ["agent memory architecture", "agent memory retrieval"]
    assert len(_node_traces(thread_id, "multi_query_search")) == 1
    assert len(_node_traces(thread_id, "deduplicate_results")) == 1
    assert [
        trace["status"]
        for trace in _node_traces(thread_id, "request_paper_selection")
    ] == ["interrupted", "success"]
    resumed_connection.close()


def test_paper_selection_finishes_without_running_report_nodes(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    planner_calls = 0
    search_queries: list[str] = []
    prepared_ids: list[int] = []
    retrieved_ids: list[int] = []
    model_calls = 0

    def plan(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return _planned_response(*args, **kwargs)

    def prepare(paper_id: int):
        prepared_ids.append(paper_id)
        return {"tool_call": None, "access_limitation": None}

    def retrieve(paper_id: int, _question: str):
        retrieved_ids.append(paper_id)
        return {
            "tool_call": {
                "tool_name": "retrieve_paper_evidence",
                "input": {"paper_id": paper_id},
                "status": "succeeded",
            },
            "evidence": [
                {
                    "paper_id": paper_id,
                    "paper_title": f"Paper {paper_id}",
                    "page_number": paper_id - 100,
                    "chunk_id": paper_id,
                    "score": 0.9,
                    "content": f"Evidence from paper {paper_id}.",
                }
            ],
            "access_limitation": None,
        }

    def generate(*_args):
        nonlocal model_calls
        model_calls += 1
        return (
            "Findings [Paper 101, p. 1] and [Paper 102, p. 2].",
            "deepseek-test",
        )

    monkeypatch.setattr(research_agent, "ask_deepseek_json", plan)
    monkeypatch.setattr(
        research_tools,
        "search_many",
        _batch_search(
            lambda query, _max_results: search_queries.append(query) or [_paper()]
        ),
    )
    monkeypatch.setattr(research_agent, "prepare_report_paper", prepare)
    monkeypatch.setattr(research_agent, "retrieve_report_paper_evidence", retrieve)
    monkeypatch.setattr(research_agent, "generate_report_content", generate)
    checkpoint_path = tmp_path / "report-confirmation.db"
    thread_id = "checkpoint-report-confirmation"
    config = _config(thread_id)

    first_connection, first_graph = _graph(checkpoint_path)
    first_graph.invoke(
        {"question": "How is memory stored?", "max_results": 5},
        config=config,
    )
    first_graph.invoke(Command(resume={"approved": True}), config=config)
    first_graph.update_state(config, {"candidate_paper_ids": [101, 102]})
    report_waiting = first_graph.invoke(
        Command(resume={"paper_ids": [101, 102], "cancelled": False}),
        config=config,
    )
    final_state = first_graph.get_state(config)

    assert "__interrupt__" not in report_waiting
    assert final_state.values["workflow_status"] == "papers_selected"
    assert final_state.next == ()
    assert planner_calls == 1
    assert search_queries == ["agent memory architecture", "agent memory retrieval"]
    assert prepared_ids == []
    assert retrieved_ids == []
    assert model_calls == 0
    assert len(_node_traces(thread_id, "multi_query_search")) == 1
    assert _node_traces(thread_id, "request_report_confirmation") == []
    assert _node_traces(thread_id, "prepare_report_paper") == []
    assert _node_traces(thread_id, "retrieve_report_evidence") == []
    assert _node_traces(thread_id, "synthesize_research_report") == []
    first_connection.close()

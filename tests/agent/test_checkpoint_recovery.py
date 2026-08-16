import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from my_agent_project import agent_trace_service, db, research_agent, research_tools
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
    monkeypatch.setattr(research_tools, "search_all", search)
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
        "search_all",
        lambda query, _max_results: search_queries.append(query) or [_paper()],
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

    assert "__interrupt__" in selected
    assert selected["workflow_status"] == "papers_selected"
    assert resumed_graph.get_state(config).next == ("request_report_confirmation",)
    assert planner_calls == 1
    assert search_queries == ["agent memory architecture", "agent memory retrieval"]
    assert len(_node_traces(thread_id, "multi_query_search")) == 1
    assert len(_node_traces(thread_id, "deduplicate_results")) == 1
    assert [
        trace["status"]
        for trace in _node_traces(thread_id, "request_paper_selection")
    ] == ["interrupted", "success"]
    resumed_connection.close()


def test_report_confirmation_and_partial_progress_resume_without_replay(
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
        "search_all",
        lambda query, _max_results: search_queries.append(query) or [_paper()],
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
    assert "__interrupt__" in report_waiting
    first_connection.close()

    second_connection, second_graph = _graph(checkpoint_path)
    stream = second_graph.stream(
        Command(resume={"approved": True}),
        config=config,
        stream_mode="values",
    )
    for values in stream:
        if values.get("report_prepare_index") == 1:
            break
    stream.close()
    assert prepared_ids == [101]
    second_connection.close()

    final_connection, final_graph = _graph(checkpoint_path)
    final_graph.invoke(None, config=config)
    final_state = final_graph.get_state(config)

    assert final_state.values["workflow_status"] == "report_completed"
    assert final_state.next == ("request_report_regeneration",)
    assert planner_calls == 1
    assert search_queries == ["agent memory architecture", "agent memory retrieval"]
    assert prepared_ids == [101, 102]
    assert retrieved_ids == [101, 102]
    assert model_calls == 1
    assert len(_node_traces(thread_id, "multi_query_search")) == 1
    assert len(_node_traces(thread_id, "prepare_report_paper")) == 2
    assert len(_node_traces(thread_id, "retrieve_report_evidence")) == 2
    assert len(_node_traces(thread_id, "synthesize_research_report")) == 1
    assert [
        trace["status"]
        for trace in _node_traces(thread_id, "request_report_confirmation")
    ] == ["interrupted", "success"]
    final_connection.close()

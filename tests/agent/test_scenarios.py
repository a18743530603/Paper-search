import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from my_agent_project import db
from my_agent_project import model_service
from my_agent_project import research_agent
from my_agent_project import research_report_service
from my_agent_project import research_tools
from my_agent_project import search_service
from my_agent_project.schemas import PaperCandidate, STATUS_AVAILABLE


def _paper(
    title: str = "Agent Memory Systems",
    *,
    source: str = "arxiv",
    status: str = STATUS_AVAILABLE,
) -> PaperCandidate:
    return PaperCandidate(
        title=title,
        authors="Test Author",
        summary="A controlled test paper.",
        published="2026-01-01",
        source=source,
        publisher="Test Publisher",
        doi=None,
        page_url="https://example.org/paper",
        pdf_url=(
            "https://example.org/paper.pdf"
            if status == STATUS_AVAILABLE
            else None
        ),
        status=status,
    )


def _planned_response(*_args, **_kwargs):
    return (
        {
            "goal": "Study agent memory",
            "queries": ["agent memory architecture", "agent memory retrieval"],
            "rationale": "Cover storage and retrieval.",
        },
        "deepseek-test",
    )


def _batch_search(search_one):
    def search(queries, *, sources, max_results):
        batches = []
        for query in queries:
            papers = search_one(query, max_results)
            batches.append(
                search_service.McpSearchBatch(
                    query=query,
                    candidates=papers,
                    source_counts={},
                    errors={},
                    raw_total=len(papers),
                )
            )
        return batches

    return search


def test_scenario_01_planner_failure_uses_original_question_once(monkeypatch):
    def fail_planner(*_args, **_kwargs):
        raise model_service.ModelRequestError("planner unavailable")

    search_queries: list[str] = []
    monkeypatch.setattr(research_agent, "ask_deepseek_json", fail_planner)
    monkeypatch.setattr(
        research_tools,
        "search_many",
        _batch_search(
            lambda query, _max_results: search_queries.append(query) or [_paper()]
        ),
    )

    result = research_agent.run_research_search("How is memory stored?", 5)

    assert result.status == research_agent.PLANNER_STATUS_FALLBACK
    assert result.queries == ["How is memory stored?"]
    assert search_queries == ["How is memory stored?"]
    assert "planner unavailable" in result.error


def test_scenario_02_rejected_plan_never_calls_search_tool(tmp_path, monkeypatch):
    search_calls = 0

    def fail_if_searched(*_args, **_kwargs):
        nonlocal search_calls
        search_calls += 1
        raise AssertionError("search must not run after rejection")

    monkeypatch.setattr(research_agent, "ask_deepseek_json", _planned_response)
    monkeypatch.setattr(
        research_tools,
        "search_many",
        _batch_search(fail_if_searched),
    )
    connection = sqlite3.connect(tmp_path / "rejected-plan.db", check_same_thread=False)
    graph = research_agent.build_checkpointed_research_graph(SqliteSaver(connection))
    config = {"configurable": {"thread_id": "scenario-rejected-plan"}}

    waiting = graph.invoke(
        {"question": "How is memory stored?", "max_results": 5},
        config=config,
    )
    rejected = graph.invoke(Command(resume={"approved": False}), config=config)

    assert "__interrupt__" in waiting
    assert rejected["workflow_status"] == "cancelled"
    assert search_calls == 0
    connection.close()


def test_scenario_03_unregistered_tool_is_blocked_without_side_effect(monkeypatch):
    side_effect_count = 0

    class RegisteredTool:
        def invoke(self, _arguments):
            nonlocal side_effect_count
            side_effect_count += 1
            return {"status": "succeeded"}

    monkeypatch.setattr(
        research_tools,
        "RESEARCH_TOOL_REGISTRY",
        {"search_literature": RegisteredTool()},
    )

    with pytest.raises(ValueError, match="未注册的研究工具"):
        research_tools.invoke_research_tool("delete_database", {})

    assert side_effect_count == 0


def test_scenario_04_one_search_source_failure_returns_partial_result(monkeypatch):
    def partial_search(_queries, *, sources, max_results):
        assert tuple(sources) == ("arxiv", "crossref")
        assert max_results == 4
        return [
            search_service.McpSearchBatch(
                query="agent memory",
                candidates=[_paper(source="crossref")],
                source_counts={"arxiv": 0, "crossref": 1},
                errors={"arxiv": "arXiv unavailable"},
                raw_total=1,
            )
        ]

    monkeypatch.setattr(research_tools, "search_many", partial_search)

    candidates, tool_calls = research_tools.search_literature_batch(
        ["agent memory"],
        sources=["arxiv", "crossref"],
        max_results=4,
    )
    result = tool_calls[0]

    assert result["status"] == "partial"
    assert result["result_count"] == 1
    assert result["source_counts"] == {"crossref": 1}
    assert "arXiv unavailable" in result["error"]
    assert len(candidates) == 1
    assert candidates[0]["source"] == "crossref"


def test_scenario_05_one_paper_failure_does_not_stop_remaining_papers(monkeypatch):
    prepared_ids: list[int] = []

    def prepare(paper_id: int):
        prepared_ids.append(paper_id)
        if paper_id == 1:
            return {
                "tool_call": {
                    "tool_name": "prepare_paper_for_research",
                    "status": "failed",
                    "error": "PDF parse failed",
                },
                "access_limitation": {
                    "paper_id": paper_id,
                    "paper_title": "Paper 1",
                    "url": "https://example.org/1",
                    "reason": "PDF parse failed",
                },
            }
        return {
            "tool_call": {
                "tool_name": "prepare_paper_for_research",
                "status": "succeeded",
                "error": "",
            },
            "access_limitation": None,
        }

    monkeypatch.setattr(research_agent, "prepare_report_paper", prepare)
    state = {
        "selected_paper_ids": [1, 2, 3],
        "report_prepare_index": 0,
        "report_tool_calls": [],
        "report_access_limitations": [],
    }

    for _ in state["selected_paper_ids"]:
        state.update(research_agent.prepare_report_paper_node(state))

    assert prepared_ids == [1, 2, 3]
    assert state["report_prepare_index"] == 3
    assert [call["status"] for call in state["report_tool_calls"]] == [
        "failed",
        "succeeded",
        "succeeded",
    ]
    assert state["report_access_limitations"][0]["paper_id"] == 1


def test_scenario_06_no_valid_evidence_skips_report_model(monkeypatch):
    model_calls = 0

    def fail_if_called(*_args, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("report model must not run without evidence")

    monkeypatch.setattr(research_report_service, "ask_deepseek", fail_if_called)
    state = {
        "question": "What does the paper conclude?",
        "report_evidence": [],
        "report_access_limitations": [],
    }
    state.update(research_agent.begin_report_generation(state))

    result = research_agent.synthesize_research_report(state)

    assert result["report_model"] == "deterministic-no-evidence"
    assert "不能可靠回答" in result["report_content"]
    assert model_calls == 0


def test_scenario_07_checkpoint_restart_does_not_repeat_planner(tmp_path, monkeypatch):
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
    checkpoint_path = tmp_path / "restart.db"
    config = {"configurable": {"thread_id": "scenario-restart"}}

    first_connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    first_graph = research_agent.build_checkpointed_research_graph(
        SqliteSaver(first_connection)
    )
    first_graph.invoke(
        {"question": "How is memory stored?", "max_results": 5},
        config=config,
    )
    first_connection.close()

    resumed_connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    resumed_graph = research_agent.build_checkpointed_research_graph(
        SqliteSaver(resumed_connection)
    )
    resumed = resumed_graph.invoke(
        Command(resume={"approved": True}),
        config=config,
    )

    assert "__interrupt__" in resumed
    assert planner_calls == 1
    assert search_queries == ["agent memory architecture", "agent memory retrieval"]
    assert resumed_graph.get_state(config).values["workflow_status"] == (
        "awaiting_selection"
    )
    resumed_connection.close()


def test_scenario_08_duplicate_triggers_claim_only_one_job(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    paper_id = db.insert_papers("agent memory", [_paper()])[0]

    download_barrier = Barrier(2)

    def claim_download():
        download_barrier.wait()
        return db.claim_download(paper_id, (STATUS_AVAILABLE,))

    with ThreadPoolExecutor(max_workers=2) as executor:
        download_claims = list(executor.map(lambda _index: claim_download(), range(2)))

    task_id = db.create_research_task(
        question="How is memory stored?",
        max_results=5,
        plan_goal="Study agent memory",
        plan_queries=["agent memory"],
        plan_rationale="",
        plan_model="deepseek-test",
        planner_status="planned",
        planner_error="",
        status="awaiting_confirmation",
    )
    assert db.start_research_task(task_id)
    db.save_research_task_results(task_id, [paper_id])
    assert db.select_research_task_papers(task_id, [paper_id])
    report_barrier = Barrier(2)

    def claim_report():
        report_barrier.wait()
        return db.start_research_report(task_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        report_claims = list(executor.map(lambda _index: claim_report(), range(2)))

    assert sorted(download_claims) == [False, True]
    assert sorted(report_claims) == [False, True]
    assert db.get_paper(paper_id)["status"] == "downloading"
    assert db.get_research_report(task_id)["status"] == "preparing"

import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from my_agent_project import agent_trace_service, db, research_agent


def _create_research_task(thread_id: str) -> int:
    return db.create_research_task(
        question="How do agent checkpoints work?",
        max_results=5,
        plan_goal="Study agent checkpoints",
        plan_queries=["agent checkpoint recovery", "LangGraph persistence"],
        plan_rationale="Cover recovery and persistence.",
        plan_model="deepseek-test",
        planner_status="planned",
        planner_error="",
        status="awaiting_confirmation",
        checkpoint_thread_id=thread_id,
    )


def _trace_config(thread_id: str, task_id: int | None = None) -> dict:
    configurable = {
        "thread_id": thread_id,
        agent_trace_service.TRACE_ENABLED_KEY: True,
    }
    if task_id is not None:
        configurable[agent_trace_service.TRACE_TASK_ID_KEY] = task_id
    return {"configurable": configurable}


def test_agent_trace_lifecycle_and_task_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()

    trace_id = db.create_agent_trace(
        task_id=None,
        thread_id="trace-attachment",
        node_name="multi_query_search",
        start_time="2026-08-16T10:00:00.000+08:00",
    )
    db.finish_agent_trace(
        trace_id,
        status="partial",
        end_time="2026-08-16T10:00:00.125+08:00",
        duration_ms=125,
        tool_calls=[
            {
                "tool_name": "search_literature",
                "status": "partial",
                "error": "Crossref unavailable",
            }
        ],
        error="Crossref unavailable",
    )
    task_id = _create_research_task("trace-attachment")

    assert db.attach_agent_traces_to_task("trace-attachment", task_id) == 1
    trace = db.list_agent_traces(task_id=task_id)[0]
    assert trace["thread_id"] == "trace-attachment"
    assert trace["node_name"] == "multi_query_search"
    assert trace["status"] == "partial"
    assert trace["duration_ms"] == 125
    assert trace["retry_count"] == 0
    assert trace["tool_calls"][0]["tool_name"] == "search_literature"
    assert trace["model_calls"] is None
    assert trace["input_tokens"] is None
    assert trace["output_tokens"] is None


def test_trace_node_records_failure_and_preserves_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()

    def failing_node(_state):
        raise RuntimeError("node failed")

    traced = agent_trace_service.trace_node("failing_node", failing_node)

    with pytest.raises(RuntimeError, match="node failed"):
        traced({}, _trace_config("trace-failure"))

    trace = db.list_agent_traces(thread_id="trace-failure")[0]
    assert trace["status"] == "failed"
    assert trace["duration_ms"] >= 0
    assert "RuntimeError: node failed" in trace["error"]


def test_trace_storage_failure_does_not_change_node_result(monkeypatch):
    def fail_to_store(**_kwargs):
        raise sqlite3.OperationalError("trace database unavailable")

    monkeypatch.setattr(agent_trace_service, "create_agent_trace", fail_to_store)
    traced = agent_trace_service.trace_node(
        "healthy_node",
        lambda state: {"value": state["value"] + 1},
    )

    assert traced({"value": 4}, _trace_config("trace-best-effort")) == {"value": 5}


def test_checkpoint_interrupt_and_resume_are_traced_as_separate_attempts(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    monkeypatch.setattr(
        research_agent,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (
            {
                "goal": "Study agent checkpoints",
                "queries": ["agent checkpoint recovery", "LangGraph persistence"],
                "rationale": "Cover recovery and persistence.",
            },
            "deepseek-test",
        ),
    )

    checkpoint_path = tmp_path / "checkpoints.db"
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    graph = research_agent.build_checkpointed_research_graph(SqliteSaver(connection))
    thread_id = "trace-interrupt-resume"
    first_config = _trace_config(thread_id)

    first_result = graph.invoke(
        {"question": "How do checkpoints work?", "max_results": 5},
        config=first_config,
    )
    assert "__interrupt__" in first_result
    task_id = _create_research_task(thread_id)
    assert db.attach_agent_traces_to_task(thread_id, task_id) == 2

    resumed = graph.invoke(
        Command(resume={"approved": False}),
        config=_trace_config(thread_id, task_id),
    )
    assert resumed["workflow_status"] == "cancelled"

    traces = db.list_agent_traces(task_id=task_id)
    plan_traces = [item for item in traces if item["node_name"] == "plan_research"]
    confirmation_traces = [
        item
        for item in traces
        if item["node_name"] == "request_plan_confirmation"
    ]
    cancel_traces = [item for item in traces if item["node_name"] == "cancel_research"]
    assert [item["status"] for item in plan_traces] == ["success"]
    assert [item["status"] for item in confirmation_traces] == [
        "interrupted",
        "success",
    ]
    assert [item["retry_count"] for item in confirmation_traces] == [0, 1]
    assert [item["status"] for item in cancel_traces] == ["success"]
    assert all(item["task_id"] == task_id for item in traces)
    connection.close()

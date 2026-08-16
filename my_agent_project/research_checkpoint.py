import sqlite3
from threading import Lock
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import LANGGRAPH_CHECKPOINT_PATH, ensure_runtime_dirs
from .research_agent import (
    ResearchSearchResult,
    build_checkpointed_research_graph,
    research_result_from_state,
)
from .agent_trace_service import TRACE_ENABLED_KEY, TRACE_TASK_ID_KEY


_runtime_lock = Lock()
_checkpoint_connection: sqlite3.Connection | None = None
_checkpointer: SqliteSaver | None = None
_research_graph = None


def checkpoint_config(
    thread_id: str,
    *,
    task_id: int | None = None,
    trace_enabled: bool = True,
) -> dict[str, dict[str, Any]]:
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        TRACE_ENABLED_KEY: trace_enabled,
    }
    if task_id is not None:
        configurable[TRACE_TASK_ID_KEY] = task_id
    return {"configurable": configurable}


def get_checkpointed_research_graph():
    global _checkpoint_connection, _checkpointer, _research_graph
    if _research_graph is not None:
        return _research_graph
    with _runtime_lock:
        if _research_graph is None:
            ensure_runtime_dirs()
            _checkpoint_connection = sqlite3.connect(
                LANGGRAPH_CHECKPOINT_PATH,
                check_same_thread=False,
            )
            _checkpointer = SqliteSaver(_checkpoint_connection)
            _research_graph = build_checkpointed_research_graph(_checkpointer)
    return _research_graph


def _checkpoint_result(
    thread_id: str,
    *,
    default_status: str = "",
) -> ResearchSearchResult:
    graph = get_checkpointed_research_graph()
    snapshot = graph.get_state(checkpoint_config(thread_id))
    values: dict[str, Any] = dict(snapshot.values)
    if default_status and not values.get("workflow_status"):
        values["workflow_status"] = default_status
    return research_result_from_state(values)


def start_checkpointed_research(
    thread_id: str,
    question: str,
    max_results: int,
) -> ResearchSearchResult:
    graph = get_checkpointed_research_graph()
    graph.invoke(
        {"question": question, "max_results": max_results},
        config=checkpoint_config(thread_id),
    )
    return _checkpoint_result(thread_id, default_status="awaiting_confirmation")


def resume_plan_confirmation(
    thread_id: str,
    *,
    approved: bool,
    task_id: int | None = None,
) -> ResearchSearchResult:
    graph = get_checkpointed_research_graph()
    graph.invoke(
        Command(resume={"approved": approved}),
        config=checkpoint_config(thread_id, task_id=task_id),
    )
    return _checkpoint_result(thread_id)


def load_checkpointed_research(
    thread_id: str,
    *,
    task_id: int | None = None,
) -> ResearchSearchResult:
    return _checkpoint_result(thread_id)


def continue_checkpointed_research(
    thread_id: str,
    *,
    task_id: int | None = None,
) -> ResearchSearchResult:
    graph = get_checkpointed_research_graph()
    graph.invoke(None, config=checkpoint_config(thread_id, task_id=task_id))
    return _checkpoint_result(thread_id)


def update_checkpoint_candidate_ids(thread_id: str, paper_ids: list[int]) -> None:
    graph = get_checkpointed_research_graph()
    graph.update_state(
        checkpoint_config(thread_id),
        {"candidate_paper_ids": list(paper_ids)},
    )


def resume_paper_selection(
    thread_id: str,
    *,
    paper_ids: list[int] | None = None,
    cancelled: bool = False,
    task_id: int | None = None,
) -> ResearchSearchResult:
    graph = get_checkpointed_research_graph()
    graph.invoke(
        Command(
            resume={
                "paper_ids": list(paper_ids or []),
                "cancelled": cancelled,
            }
        ),
        config=checkpoint_config(thread_id, task_id=task_id),
    )
    return _checkpoint_result(thread_id)


def stream_checkpointed_research_report(
    thread_id: str,
    *,
    task_id: int | None = None,
):
    graph = get_checkpointed_research_graph()
    config = checkpoint_config(thread_id, task_id=task_id)
    snapshot = graph.get_state(config)
    next_nodes = set(snapshot.next)
    graph_input = (
        Command(resume={"approved": True})
        if next_nodes
        & {"request_report_confirmation", "request_report_regeneration"}
        else None
    )
    for values in graph.stream(graph_input, config=config, stream_mode="values"):
        yield dict(values)


def research_checkpoint_info(thread_id: str) -> dict[str, Any]:
    graph = get_checkpointed_research_graph()
    snapshot = graph.get_state(checkpoint_config(thread_id))
    return {
        "thread_id": thread_id,
        "next_nodes": list(snapshot.next),
        "checkpoint_id": snapshot.config.get("configurable", {}).get(
            "checkpoint_id",
            "",
        ),
        "workflow_status": snapshot.values.get("workflow_status", ""),
    }


def delete_research_checkpoint(thread_id: str) -> None:
    get_checkpointed_research_graph()
    if _checkpointer is not None:
        _checkpointer.delete_thread(thread_id)

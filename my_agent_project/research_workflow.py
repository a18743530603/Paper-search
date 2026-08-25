from .db import (
    fail_research_task,
    get_research_task,
    insert_papers,
    replace_research_tool_calls,
    save_research_task_results,
    update_research_report,
)
from .research_checkpoint import (
    continue_checkpointed_research,
    load_checkpointed_research,
    research_checkpoint_info,
    resume_plan_confirmation,
    stream_checkpointed_research_report,
    update_checkpoint_candidate_ids,
    update_checkpoint_sources,
)
from .research_agent import run_confirmed_research_search
from .research_report_service import generate_research_report
from .schemas import (
    RESEARCH_REPORT_COMPLETED,
    RESEARCH_REPORT_FAILED,
    RESEARCH_REPORT_GENERATING,
    RESEARCH_REPORT_PREPARING,
    RESEARCH_REPORT_RETRIEVING,
    RESEARCH_TASK_RUNNING,
)


REPORT_CHECKPOINT_NODES = {
    "request_report_confirmation",
    "request_report_regeneration",
    "prepare_report_paper",
    "retrieve_report_evidence",
    "begin_report_generation",
    "synthesize_research_report",
    "validate_report_citations",
}


def execute_research_task(task_id: int, *, raise_on_error: bool = False) -> None:
    task = get_research_task(task_id)
    if task is None or task["status"] != RESEARCH_TASK_RUNNING:
        return

    try:
        thread_id = task.get("checkpoint_thread_id") or ""
        if thread_id:
            update_checkpoint_sources(thread_id, task["sources"])
            checkpoint = research_checkpoint_info(thread_id)
            next_nodes = set(checkpoint["next_nodes"])
            if "request_plan_confirmation" in next_nodes:
                result = resume_plan_confirmation(
                    thread_id,
                    approved=True,
                    task_id=task_id,
                )
            elif "request_paper_selection" in next_nodes:
                result = load_checkpointed_research(thread_id, task_id=task_id)
            else:
                result = continue_checkpointed_research(thread_id, task_id=task_id)
        else:
            result = run_confirmed_research_search(
                task["question"],
                int(task["max_results"]),
                goal=task["plan_goal"],
                queries=list(task["plan_queries"]),
                rationale=task["plan_rationale"],
                model=task["plan_model"],
                status=task["planner_status"],
                error=task["planner_error"] or "",
                sources=task["sources"],
            )
        paper_ids = insert_papers(task["question"], result.candidates)
        replace_research_tool_calls(task_id, result.tool_calls)
        save_research_task_results(task_id, paper_ids)
        if thread_id:
            update_checkpoint_candidate_ids(thread_id, paper_ids)
    except Exception as exc:
        fail_research_task(task_id, f"{type(exc).__name__}: {exc}")
        if raise_on_error:
            raise


def _persist_report_graph_state(task_id: int, state: dict) -> None:
    status_map = {
        "report_preparing": RESEARCH_REPORT_PREPARING,
        "report_retrieving": RESEARCH_REPORT_RETRIEVING,
        "report_generating": RESEARCH_REPORT_GENERATING,
        "report_completed": RESEARCH_REPORT_COMPLETED,
    }
    report_status = status_map.get(state.get("workflow_status"))
    if report_status is None:
        return
    details = {
        "evidence": list(state.get("report_evidence", [])),
        "context_evidence": list(state.get("report_context_evidence", [])),
        "context_stats": dict(state.get("report_context_stats", {})),
        "context_quality": dict(state.get("report_context_quality", {})),
        "access_limitations": list(
            state.get("report_access_limitations", [])
        ),
        "tool_calls": list(state.get("report_tool_calls", [])),
        "citation_validation": dict(
            state.get("report_citation_validation", {})
        ),
        "checkpoint_progress": {
            "prepared_papers": int(state.get("report_prepare_index", 0)),
            "retrieved_papers": int(state.get("report_retrieve_index", 0)),
            "selected_papers": len(state.get("selected_paper_ids", [])),
        },
    }
    update_research_report(
        task_id,
        report_status,
        content=(
            state.get("report_content")
            if report_status == RESEARCH_REPORT_COMPLETED
            else None
        ),
        details=details,
        model=(
            state.get("report_model")
            if report_status == RESEARCH_REPORT_COMPLETED
            else None
        ),
    )


def execute_research_report(task_id: int) -> None:
    task = get_research_task(task_id)
    if task is None:
        return
    thread_id = task.get("checkpoint_thread_id") or ""
    if not thread_id:
        generate_research_report(task_id)
        return

    try:
        checkpoint = research_checkpoint_info(thread_id)
        if not set(checkpoint["next_nodes"]) & REPORT_CHECKPOINT_NODES:
            generate_research_report(task_id)
            return
        for state in stream_checkpointed_research_report(
            thread_id,
            task_id=task_id,
        ):
            _persist_report_graph_state(task_id, state)
    except Exception as exc:
        update_research_report(
            task_id,
            RESEARCH_REPORT_FAILED,
            error=f"{type(exc).__name__}: {exc}"[:1000],
        )

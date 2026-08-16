from datetime import datetime
from time import perf_counter
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt

from .db import create_agent_trace, finish_agent_trace


TRACE_ENABLED_KEY = "agent_trace_enabled"
TRACE_TASK_ID_KEY = "paper_hunter_task_id"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _trace_identity(config: RunnableConfig) -> tuple[int | None, str]:
    configurable = config.get("configurable", {})
    if not configurable.get(TRACE_ENABLED_KEY, False):
        return None, ""
    thread_id = str(configurable.get("thread_id") or "").strip()
    raw_task_id = configurable.get(TRACE_TASK_ID_KEY)
    try:
        task_id = int(raw_task_id) if raw_task_id is not None else None
    except (TypeError, ValueError):
        task_id = None
    return task_id, thread_id


def _start_trace(
    node_name: str,
    config: RunnableConfig,
) -> tuple[int | None, float]:
    started = perf_counter()
    task_id, thread_id = _trace_identity(config)
    if not thread_id:
        return None, started
    try:
        trace_id = create_agent_trace(
            task_id=task_id,
            thread_id=thread_id,
            node_name=node_name,
            start_time=_now_iso(),
        )
    except Exception:
        return None, started
    return trace_id, started


def _tool_call_delta(
    state: dict[str, Any],
    result: Any,
) -> list[dict]:
    if not isinstance(result, dict):
        return []
    for key in ("tool_calls", "report_tool_calls"):
        after = result.get(key)
        if not isinstance(after, list):
            continue
        before = state.get(key, [])
        before_count = len(before) if isinstance(before, list) else 0
        delta = after[before_count:] if len(after) >= before_count else after
        return [dict(item) for item in delta if isinstance(item, dict)]
    return []


def _result_status(tool_calls: list[dict]) -> tuple[str, str]:
    unsuccessful = [
        call
        for call in tool_calls
        if str(call.get("status") or "").lower()
        not in {"", "success", "succeeded", "completed"}
    ]
    if not unsuccessful:
        return "success", ""
    errors = [str(call.get("error") or "").strip() for call in unsuccessful]
    return "partial", "; ".join(error for error in errors if error)[:1000]


def _finish_trace(
    trace_id: int | None,
    started: float,
    *,
    status: str,
    tool_calls: list[dict] | None = None,
    error: str = "",
) -> None:
    if trace_id is None:
        return
    try:
        finish_agent_trace(
            trace_id,
            status=status,
            end_time=_now_iso(),
            duration_ms=int((perf_counter() - started) * 1000),
            tool_calls=tool_calls,
            error=error,
        )
    except Exception:
        return


def trace_node(
    node_name: str,
    node: Callable[[dict[str, Any]], dict[str, Any]],
):
    def traced_node(
        state: dict[str, Any],
        config: RunnableConfig,
    ) -> dict[str, Any]:
        trace_id, started = _start_trace(node_name, config)
        try:
            result = node(state)
        except GraphInterrupt:
            _finish_trace(trace_id, started, status="interrupted")
            raise
        except Exception as exc:
            _finish_trace(
                trace_id,
                started,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:1000],
            )
            raise

        tool_calls = _tool_call_delta(state, result)
        status, error = _result_status(tool_calls)
        _finish_trace(
            trace_id,
            started,
            status=status,
            tool_calls=tool_calls,
            error=error,
        )
        return result

    traced_node.__name__ = f"traced_{node_name}"
    traced_node.__doc__ = getattr(node, "__doc__", None)
    return traced_node

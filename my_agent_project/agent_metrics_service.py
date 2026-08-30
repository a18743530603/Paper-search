from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .db import list_agent_metric_tasks, list_agent_metric_traces


METRICS_SCHEMA_VERSION = "agent-metrics-v1"
SUCCESS_STATUSES = {"success", "succeeded", "completed"}
RECOVERY_SUCCESS_STATUSES = SUCCESS_STATUSES | {"partial"}
SEARCH_NODES = {"multi_query_search", "single_query_search"}


def _rate(
    numerator: int,
    denominator: int,
    *,
    excluded: int,
    definition: str,
    availability: str = "available",
) -> dict[str, Any]:
    return {
        "numerator": max(0, int(numerator)),
        "denominator": max(0, int(denominator)),
        "excluded": max(0, int(excluded)),
        "value_percent": (
            round(numerator / denominator * 100, 2) if denominator else None
        ),
        "availability": (
            availability
            if denominator or availability != "available"
            else "no_data"
        ),
        "definition": definition,
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _average(values: Iterable[int | float]) -> tuple[float | None, int]:
    materialized = [float(value) for value in values]
    if not materialized:
        return None, 0
    return round(sum(materialized) / len(materialized), 2), len(materialized)


def _task_outcome(task: Mapping[str, Any]) -> str:
    if task.get("task_status") in {"papers_selected", "completed"}:
        return "completed"
    if task.get("task_status") == "failed":
        return "failed"
    return "excluded"


def _task_completion_rate(tasks: list[dict]) -> dict[str, Any]:
    outcomes = Counter(_task_outcome(task) for task in tasks)
    denominator = outcomes["completed"] + outcomes["failed"]
    return _rate(
        outcomes["completed"],
        denominator,
        excluded=outcomes["excluded"],
        definition=(
            "已冻结论文集合并进入Research Workspace的任务 /（Workspace可用任务 + "
            "系统失败任务）；等待审批、等待选择和用户取消均不进入分母，"
            "可选Report成功或失败不改变任务完成结果。"
        ),
    )


def _tool_success_rate(traces: list[dict]) -> tuple[dict[str, Any], int]:
    calls = [
        call
        for trace in traces
        for call in trace.get("tool_calls", [])
        if isinstance(call, dict) and call.get("tool_name")
    ]
    successful = sum(
        str(call.get("status") or "").lower() in SUCCESS_STATUSES
        for call in calls
    )
    partial = sum(
        str(call.get("status") or "").lower() == "partial" for call in calls
    )
    return (
        _rate(
            successful,
            len(calls),
            excluded=0,
            definition="状态为success/succeeded/completed的Tool调用 / 全部有效Tool调用。",
        ),
        partial,
    )


def _approval_compliance_rate(traces: list[dict]) -> dict[str, Any]:
    by_thread: dict[str, list[dict]] = defaultdict(list)
    for trace in traces:
        by_thread[str(trace.get("thread_id") or "")].append(trace)

    rejected_threads = []
    compliant = 0
    for thread_id, thread_traces in by_thread.items():
        if not thread_id:
            continue
        rejected = any(
            trace.get("node_name") == "cancel_research"
            and trace.get("status") in RECOVERY_SUCCESS_STATUSES
            for trace in thread_traces
        )
        if not rejected:
            continue
        rejected_threads.append(thread_id)
        searched = any(
            trace.get("node_name") in SEARCH_NODES for trace in thread_traces
        )
        compliant += int(not searched)

    return _rate(
        compliant,
        len(rejected_threads),
        excluded=max(0, len(by_thread) - len(rejected_threads)),
        definition="拒绝检索计划后未执行任何搜索节点的线程 / 已拒绝计划线程。",
    )


def _checkpoint_recovery_rate(traces: list[dict]) -> dict[str, Any]:
    by_thread_node: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for trace in traces:
        key = (
            str(trace.get("thread_id") or ""),
            str(trace.get("node_name") or ""),
        )
        by_thread_node[key].append(trace)

    attempted = 0
    recovered = 0
    pending = 0
    for attempts in by_thread_node.values():
        ordered = sorted(attempts, key=lambda item: int(item.get("id") or 0))
        for index, trace in enumerate(ordered):
            if trace.get("status") != "interrupted":
                continue
            later_attempts = ordered[index + 1 :]
            if not later_attempts:
                pending += 1
                continue
            attempted += 1
            recovered += int(
                any(
                    later.get("status") in RECOVERY_SUCCESS_STATUSES
                    for later in later_attempts
                )
            )

    return _rate(
        recovered,
        attempted,
        excluded=pending,
        definition=(
            "后续同线程同节点成功或部分成功的中断 / 已发起恢复的中断；"
            "尚未恢复的等待中断不进入分母。"
        ),
    )


def _fallback_success_rate(tasks: list[dict]) -> dict[str, Any]:
    fallback_tasks = [task for task in tasks if task.get("planner_status") == "fallback"]
    succeeded = 0
    failed = 0
    excluded = 0
    for task in fallback_tasks:
        if task.get("task_status") in {"awaiting_selection", "papers_selected"}:
            succeeded += 1
        elif task.get("report_status") == "completed":
            succeeded += 1
        elif (
            task.get("task_status") == "failed"
            or task.get("report_status") == "failed"
        ):
            failed += 1
        else:
            excluded += 1
    return _rate(
        succeeded,
        succeeded + failed,
        excluded=excluded,
        definition=(
            "fallback后到达候选选择、论文已选或报告完成的任务 / "
            "已产生成功或系统失败结果的fallback任务。"
        ),
    )


def _task_latency(tasks: list[dict]) -> dict[str, Any]:
    latencies = []
    for task in tasks:
        outcome = _task_outcome(task)
        if outcome == "excluded":
            continue
        started = _parse_timestamp(task.get("created_at"))
        completed = _parse_timestamp(
            task.get("selected_at")
            or task.get("task_completed_at")
            or task.get("report_completed_at")
        )
        if started is None or completed is None or completed < started:
            continue
        latencies.append((completed - started).total_seconds() * 1000)
    average, sample_count = _average(latencies)
    return {
        "value_ms": average,
        "sample_count": sample_count,
        "definition": (
            "已决任务从research_tasks.created_at到selected_at（进入Research "
            "Workspace）的平均耗时；历史记录缺失selected_at时回退到既有完成时间。"
        ),
    }


def _node_latency(traces: list[dict]) -> dict[str, Any]:
    valid = [
        trace
        for trace in traces
        if trace.get("duration_ms") is not None and trace.get("status") != "running"
    ]
    average, sample_count = _average(
        max(0, int(trace["duration_ms"])) for trace in valid
    )
    grouped: dict[str, list[int]] = defaultdict(list)
    for trace in valid:
        grouped[str(trace.get("node_name") or "unknown")].append(
            max(0, int(trace["duration_ms"]))
        )
    by_node = []
    for node_name, durations in sorted(grouped.items()):
        node_average, node_samples = _average(durations)
        by_node.append(
            {
                "node_name": node_name,
                "value_ms": node_average,
                "sample_count": node_samples,
            }
        )
    return {
        "value_ms": average,
        "sample_count": sample_count,
        "by_node": by_node,
        "definition": "所有已结束Trace的平均节点耗时；按节点同时给出样本数。",
    }


def _distribution(traces: list[dict], status: str) -> list[dict[str, Any]]:
    counts = Counter(
        str(trace.get("node_name") or "unknown")
        for trace in traces
        if trace.get("status") == status
    )
    total = sum(counts.values())
    return [
        {
            "node_name": node_name,
            "count": count,
            "value_percent": round(count / total * 100, 2) if total else None,
        }
        for node_name, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _usage_metric(traces: list[dict], field: str) -> dict[str, Any]:
    observed = [int(trace[field]) for trace in traces if trace.get(field) is not None]
    average, sample_count = _average(observed)
    return {
        "total": sum(observed) if observed else None,
        "average_per_observed_trace": average,
        "observed_trace_count": sample_count,
        "coverage_percent": (
            round(sample_count / len(traces) * 100, 2) if traces else None
        ),
    }


def build_agent_metrics(
    *,
    tasks: list[dict] | None = None,
    traces: list[dict] | None = None,
) -> dict[str, Any]:
    active_tasks = list_agent_metric_tasks() if tasks is None else list(tasks)
    active_traces = list_agent_metric_traces() if traces is None else list(traces)
    tool_rate, partial_tool_calls = _tool_success_rate(active_traces)
    thread_count = len(
        {
            str(trace.get("thread_id") or "")
            for trace in active_traces
            if trace.get("thread_id")
        }
    )
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {
            "task_count": len(active_tasks),
            "thread_count": thread_count,
            "trace_count": len(active_traces),
            "finished_trace_count": sum(
                trace.get("status") != "running" for trace in active_traces
            ),
        },
        "rates": {
            "scenario_pass_rate": _rate(
                0,
                0,
                excluded=0,
                definition="pytest场景结果尚未持久化，将由A6.5评测报告单独注入。",
                availability="not_persisted",
            ),
            "task_completion_rate": _task_completion_rate(active_tasks),
            "tool_success_rate": tool_rate,
            "approval_compliance_rate": _approval_compliance_rate(active_traces),
            "checkpoint_recovery_rate": _checkpoint_recovery_rate(active_traces),
            "fallback_success_rate": _fallback_success_rate(active_tasks),
        },
        "latency": {
            "average_task_latency": _task_latency(active_tasks),
            "average_node_latency": _node_latency(active_traces),
        },
        "failures": {
            "failed_by_node": _distribution(active_traces, "failed"),
            "partial_by_node": _distribution(active_traces, "partial"),
            "partial_tool_call_count": partial_tool_calls,
        },
        "usage": {
            "model_calls": _usage_metric(active_traces, "model_calls"),
            "input_tokens": _usage_metric(active_traces, "input_tokens"),
            "output_tokens": _usage_metric(active_traces, "output_tokens"),
            "note": "仅聚合Trace中真实持久化的usage；无观测值时返回null。",
        },
    }

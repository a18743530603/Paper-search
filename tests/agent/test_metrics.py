from my_agent_project import agent_metrics_service, db, main


def _task(
    task_id: int,
    *,
    planner_status: str = "planned",
    task_status: str = "papers_selected",
    report_status: str | None = None,
    selected_at: str | None = None,
    task_completed_at: str | None = None,
    report_completed_at: str | None = None,
) -> dict:
    return {
        "id": task_id,
        "checkpoint_thread_id": f"thread-{task_id}",
        "planner_status": planner_status,
        "task_status": task_status,
        "created_at": "2026-08-16 10:00:00",
        "confirmed_at": "2026-08-16 10:00:01",
        "selected_at": (
            selected_at
            if selected_at is not None
            else (
                "2026-08-16 10:00:10"
                if task_status == "papers_selected"
                else None
            )
        ),
        "task_completed_at": task_completed_at,
        "report_status": report_status,
        "report_started_at": "2026-08-16 10:00:02" if report_status else None,
        "report_completed_at": report_completed_at,
    }


def _trace(
    trace_id: int,
    thread_id: str,
    node_name: str,
    status: str,
    duration_ms: int,
    *,
    tool_calls: list[dict] | None = None,
    model_calls: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict:
    return {
        "id": trace_id,
        "task_id": None,
        "thread_id": thread_id,
        "node_name": node_name,
        "status": status,
        "start_time": "2026-08-16T10:00:00+08:00",
        "end_time": "2026-08-16T10:00:01+08:00",
        "duration_ms": duration_ms,
        "tool_calls": tool_calls or [],
        "model_calls": model_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "retry_count": 0,
        "error": None,
    }


def test_agent_metrics_empty_dataset_returns_null_rates():
    metrics = agent_metrics_service.build_agent_metrics(tasks=[], traces=[])

    assert metrics["schema_version"] == "agent-metrics-v1"
    assert metrics["scope"] == {
        "task_count": 0,
        "thread_count": 0,
        "trace_count": 0,
        "finished_trace_count": 0,
    }
    for name, rate in metrics["rates"].items():
        assert rate["denominator"] == 0, name
        assert rate["value_percent"] is None, name
    assert metrics["rates"]["scenario_pass_rate"]["availability"] == (
        "not_persisted"
    )
    assert metrics["latency"]["average_task_latency"]["value_ms"] is None
    assert metrics["usage"]["input_tokens"]["total"] is None


def test_agent_metrics_use_explicit_denominators_and_real_usage_only():
    tasks = [
        _task(
            1,
            report_status="completed",
            report_completed_at="2026-08-16 10:00:10",
        ),
        _task(
            2,
            task_status="failed",
            task_completed_at="2026-08-16 10:00:20",
        ),
        _task(3, task_status="awaiting_selection"),
        _task(4, planner_status="fallback", task_status="awaiting_selection"),
        _task(
            5,
            planner_status="fallback",
            task_status="failed",
            task_completed_at="2026-08-16 10:00:30",
        ),
    ]
    traces = [
        _trace(1, "rejected", "request_plan_confirmation", "interrupted", 2),
        _trace(2, "rejected", "request_plan_confirmation", "success", 3),
        _trace(3, "rejected", "cancel_research", "success", 1),
        _trace(
            4,
            "tools",
            "multi_query_search",
            "partial",
            10,
            tool_calls=[
                {"tool_name": "search_literature", "status": "succeeded"},
                {"tool_name": "search_literature", "status": "partial"},
            ],
        ),
        _trace(
            5,
            "report",
            "synthesize_research_report",
            "failed",
            20,
            model_calls=1,
            input_tokens=100,
            output_tokens=50,
        ),
        _trace(6, "pending", "request_paper_selection", "interrupted", 4),
    ]

    metrics = agent_metrics_service.build_agent_metrics(tasks=tasks, traces=traces)

    task_rate = metrics["rates"]["task_completion_rate"]
    assert (task_rate["numerator"], task_rate["denominator"]) == (1, 3)
    assert task_rate["excluded"] == 2
    assert task_rate["value_percent"] == 33.33
    tool_rate = metrics["rates"]["tool_success_rate"]
    assert (tool_rate["numerator"], tool_rate["denominator"]) == (1, 2)
    assert tool_rate["value_percent"] == 50.0
    assert metrics["rates"]["approval_compliance_rate"]["value_percent"] == 100.0
    recovery_rate = metrics["rates"]["checkpoint_recovery_rate"]
    assert (recovery_rate["numerator"], recovery_rate["denominator"]) == (1, 1)
    assert recovery_rate["excluded"] == 1
    fallback_rate = metrics["rates"]["fallback_success_rate"]
    assert (fallback_rate["numerator"], fallback_rate["denominator"]) == (1, 2)
    assert metrics["latency"]["average_task_latency"] == {
        "value_ms": 20000.0,
        "sample_count": 3,
        "definition": metrics["latency"]["average_task_latency"]["definition"],
    }
    assert metrics["failures"]["failed_by_node"][0]["node_name"] == (
        "synthesize_research_report"
    )
    assert metrics["failures"]["partial_tool_call_count"] == 1
    assert metrics["usage"]["model_calls"]["total"] == 1
    assert metrics["usage"]["input_tokens"]["total"] == 100
    assert metrics["usage"]["output_tokens"]["total"] == 50
    assert metrics["usage"]["input_tokens"]["observed_trace_count"] == 1


def test_optional_report_failure_does_not_fail_workspace_task():
    metrics = agent_metrics_service.build_agent_metrics(
        tasks=[_task(1, report_status="failed")],
        traces=[],
    )

    rate = metrics["rates"]["task_completion_rate"]
    assert (rate["numerator"], rate["denominator"], rate["excluded"]) == (
        1,
        1,
        0,
    )


def test_agent_metrics_load_runtime_rows_from_temporary_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    task_id = db.create_research_task(
        question="How does memory work?",
        max_results=5,
        plan_goal="Study memory",
        plan_queries=["agent memory"],
        plan_rationale="",
        plan_model="deepseek-test",
        planner_status="planned",
        planner_error="",
        status="awaiting_confirmation",
        checkpoint_thread_id="metric-runtime",
    )
    trace_id = db.create_agent_trace(
        task_id=task_id,
        thread_id="metric-runtime",
        node_name="plan_research",
        start_time="2026-08-16T10:00:00+08:00",
    )
    db.finish_agent_trace(
        trace_id,
        status="success",
        end_time="2026-08-16T10:00:00.010+08:00",
        duration_ms=10,
    )

    metrics = agent_metrics_service.build_agent_metrics()

    assert metrics["scope"] == {
        "task_count": 1,
        "thread_count": 1,
        "trace_count": 1,
        "finished_trace_count": 1,
    }
    assert metrics["latency"]["average_node_latency"]["value_ms"] == 10.0
    assert metrics["rates"]["task_completion_rate"]["denominator"] == 0


def test_agent_metrics_api_returns_service_payload(monkeypatch):
    expected = {
        "schema_version": "agent-metrics-v1",
        "scope": {"trace_count": 3},
    }
    monkeypatch.setattr(main, "build_agent_metrics", lambda: expected)

    assert main.agent_metrics() == expected

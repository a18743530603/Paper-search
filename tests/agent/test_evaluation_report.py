import json

from my_agent_project import agent_evaluation_service, agent_metrics_service


def _result(
    scenario_id: str,
    status: str,
    *,
    title: str = "Controlled scenario",
) -> dict:
    return {
        "scenario_id": scenario_id,
        "suite": "test",
        "test_name": f"test_{scenario_id.lower()}",
        "title": title,
        "expected_behavior": "Expected behavior.",
        "status": status,
        "duration_ms": 10.0,
        "error": "failure detail" if status == "failed" else "",
    }


def test_scenario_manifest_contains_unique_frozen_nodeids():
    nodeids = agent_evaluation_service.scenario_test_nodeids()

    assert len(nodeids) == 11
    assert len(set(nodeids)) == 11
    assert all(nodeid.startswith("tests/agent/") for nodeid in nodeids)


def test_parse_junit_reports_pass_fail_skip_and_missing(tmp_path):
    scenarios = (
        agent_evaluation_service.AgentScenario(
            "S-1", "test", "test_pass", "Pass", "Passes."
        ),
        agent_evaluation_service.AgentScenario(
            "S-2", "test", "test_fail", "Fail", "Fails."
        ),
        agent_evaluation_service.AgentScenario(
            "S-3", "test", "test_skip", "Skip", "Skips."
        ),
        agent_evaluation_service.AgentScenario(
            "S-4", "test", "test_missing", "Missing", "Runs."
        ),
    )
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite tests="3">
    <testcase name="test_pass" time="0.010" />
    <testcase name="test_fail" time="0.020">
      <failure message="assertion failed" />
    </testcase>
    <testcase name="test_skip" time="0.000">
      <skipped message="unsupported" />
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    results = agent_evaluation_service.parse_junit_scenario_results(
        junit_path,
        scenarios=scenarios,
    )

    assert [item["status"] for item in results] == [
        "passed",
        "failed",
        "skipped",
        "not_run",
    ]
    assert results[0]["duration_ms"] == 10.0
    assert results[1]["error"] == "assertion failed"
    assert "缺少" in results[3]["error"]


def test_report_keeps_controlled_scenarios_separate_from_runtime_metrics():
    runtime_metrics = agent_metrics_service.build_agent_metrics(tasks=[], traces=[])
    report = agent_evaluation_service.build_agent_evaluation_report(
        [_result("S-1", "passed"), _result("S-2", "failed")],
        runtime_metrics,
        project_version="0.11.4",
        pytest_exit_code=1,
    )
    text = agent_evaluation_service.render_agent_evaluation_text(report)

    assert report["overall_status"] == "failed"
    assert report["controlled_scenarios"]["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "pass_rate": {
            "numerator": 1,
            "denominator": 2,
            "excluded": 0,
            "value_percent": 50.0,
            "definition": "通过的冻结场景 / 报告期望执行的全部冻结场景。",
        },
    }
    assert report["runtime_observability"]["metrics"]["scope"]["trace_count"] == 0
    assert "Scenario Pass Rate: 50.00% (1/2, excluded=0)" in text
    assert "Task Completion Rate: N/A (0/0, excluded=0)" in text
    assert "[FAILED] S-2" in text


def test_save_report_writes_timestamped_and_latest_artifacts(tmp_path):
    runtime_metrics = agent_metrics_service.build_agent_metrics(tasks=[], traces=[])
    report = agent_evaluation_service.build_agent_evaluation_report(
        [_result("S-1", "passed")],
        runtime_metrics,
        project_version="0.11.4",
        pytest_exit_code=0,
    )

    paths = agent_evaluation_service.save_agent_evaluation_report(
        report,
        output_dir=tmp_path,
    )

    assert report["overall_status"] == "passed"
    assert set(paths) == {"json", "text", "latest_json", "latest_text"}
    assert all(path.is_file() for path in paths.values())
    saved = json.loads(paths["latest_json"].read_text(encoding="utf-8"))
    assert saved["schema_version"] == "agent-evaluation-report-v1"
    assert saved["controlled_scenarios"]["summary"]["passed"] == 1
    assert "Overall Status: PASSED" in paths["latest_text"].read_text(
        encoding="utf-8"
    )

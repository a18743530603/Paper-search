import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree

from .config import BASE_DIR


EVALUATION_REPORT_SCHEMA_VERSION = "agent-evaluation-report-v1"
AGENT_EVALUATION_OUTPUT_DIR = BASE_DIR / "outputs" / "agent_evaluation"


@dataclass(frozen=True)
class AgentScenario:
    scenario_id: str
    suite: str
    test_name: str
    title: str
    expected_behavior: str


AGENT_SCENARIOS = (
    AgentScenario(
        "A6.2-01",
        "fixed_scenarios",
        "test_scenario_01_planner_failure_uses_original_question_once",
        "Planner失败降级",
        "使用原问题执行一次单查询fallback。",
    ),
    AgentScenario(
        "A6.2-02",
        "fixed_scenarios",
        "test_scenario_02_rejected_plan_never_calls_search_tool",
        "人工拒绝计划",
        "拒绝后工作流取消且搜索Tool调用为0。",
    ),
    AgentScenario(
        "A6.2-03",
        "fixed_scenarios",
        "test_scenario_03_unregistered_tool_is_blocked_without_side_effect",
        "非法Tool拦截",
        "Registry在执行前拒绝未知Tool且无副作用。",
    ),
    AgentScenario(
        "A6.2-04",
        "fixed_scenarios",
        "test_scenario_04_one_search_source_failure_returns_partial_result",
        "搜索源故障降级",
        "单来源失败时保留健康来源并标记partial。",
    ),
    AgentScenario(
        "A6.2-05",
        "fixed_scenarios",
        "test_scenario_05_one_paper_failure_does_not_stop_remaining_papers",
        "单篇论文失败隔离",
        "失败论文记录限制，其余论文继续处理。",
    ),
    AgentScenario(
        "A6.2-06",
        "fixed_scenarios",
        "test_scenario_06_no_valid_evidence_skips_report_model",
        "Evidence质量门禁",
        "无合法证据时确定性拒答且模型调用为0。",
    ),
    AgentScenario(
        "A6.2-07",
        "fixed_scenarios",
        "test_scenario_07_checkpoint_restart_does_not_repeat_planner",
        "基础Checkpoint恢复",
        "跨连接恢复后Planner不重复执行。",
    ),
    AgentScenario(
        "A6.2-08",
        "fixed_scenarios",
        "test_scenario_08_duplicate_triggers_claim_only_one_job",
        "重复触发幂等",
        "并发下载与报告触发均只有一个请求取得资格。",
    ),
    AgentScenario(
        "A6.3-01",
        "checkpoint_recovery",
        "test_plan_confirmation_recovers_with_trace_and_without_replanning",
        "计划确认恢复",
        "恢复确认节点且Planner与搜索不发生越界调用。",
    ),
    AgentScenario(
        "A6.3-02",
        "checkpoint_recovery",
        "test_paper_selection_recovers_without_repeating_plan_or_search",
        "论文选择恢复",
        "恢复选择节点且规划、搜索、去重不重放。",
    ),
    AgentScenario(
        "A6.3-03",
        "checkpoint_recovery",
        "test_report_confirmation_and_partial_progress_resume_without_replay",
        "报告阶段中途恢复",
        "从下一篇论文继续，已完成节点和模型调用不重放。",
    ),
)


def scenario_test_nodeids() -> list[str]:
    paths = {
        "fixed_scenarios": "tests/agent/test_scenarios.py",
        "checkpoint_recovery": "tests/agent/test_checkpoint_recovery.py",
    }
    return [
        f"{paths[scenario.suite]}::{scenario.test_name}"
        for scenario in AGENT_SCENARIOS
    ]


def _testcase_status(testcase: ElementTree.Element) -> tuple[str, str]:
    for child_name, status in (
        ("failure", "failed"),
        ("error", "failed"),
        ("skipped", "skipped"),
    ):
        child = testcase.find(child_name)
        if child is not None:
            message = str(child.get("message") or child.text or "").strip()
            return status, message[:2000]
    return "passed", ""


def parse_junit_scenario_results(
    junit_path: Path,
    *,
    scenarios: Iterable[AgentScenario] = AGENT_SCENARIOS,
) -> list[dict[str, Any]]:
    root = ElementTree.parse(junit_path).getroot()
    testcases = {
        str(testcase.get("name") or ""): testcase
        for testcase in root.iter("testcase")
    }
    results = []
    for scenario in scenarios:
        testcase = testcases.get(scenario.test_name)
        if testcase is None:
            status, error, duration_ms = "not_run", "JUnit中缺少该场景", None
        else:
            status, error = _testcase_status(testcase)
            try:
                duration_ms = round(float(testcase.get("time") or 0) * 1000, 2)
            except ValueError:
                duration_ms = None
        item = asdict(scenario)
        item.update(
            {
                "status": status,
                "duration_ms": duration_ms,
                "error": error,
            }
        )
        results.append(item)
    return results


def _scenario_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(item.get("status") == "passed" for item in results)
    failed = sum(item.get("status") in {"failed", "not_run"} for item in results)
    skipped = sum(item.get("status") == "skipped" for item in results)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": {
            "numerator": passed,
            "denominator": total,
            "excluded": 0,
            "value_percent": round(passed / total * 100, 2) if total else None,
            "definition": "通过的冻结场景 / 报告期望执行的全部冻结场景。",
        },
    }


def build_agent_evaluation_report(
    scenario_results: list[dict[str, Any]],
    runtime_metrics: Mapping[str, Any],
    *,
    project_version: str,
    pytest_exit_code: int | None = None,
) -> dict[str, Any]:
    summary = _scenario_summary(scenario_results)
    if not scenario_results:
        overall_status = "not_run"
    elif summary["passed"] == summary["total"] and pytest_exit_code in {None, 0}:
        overall_status = "passed"
    else:
        overall_status = "failed"
    return {
        "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
        "project_version": project_version,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "controlled_scenarios": {
            "source": "pytest+junitxml",
            "pytest_exit_code": pytest_exit_code,
            "summary": summary,
            "results": scenario_results,
        },
        "runtime_observability": {
            "source": "research_tasks+research_reports+agent_traces",
            "metrics": dict(runtime_metrics),
        },
        "limitations": [
            "受控pytest场景不等同于真实线上成功率或SLA。",
            "运行指标依赖当前SQLite中实际保存的任务和Trace样本，空分母返回null。",
            "模型调用和Token仅在API真实usage已持久化时统计，不使用估算值。",
            "Checkpoint恢复结论不包含进程内BackgroundTasks的跨重启恢复。",
        ],
    }


def _format_rate(rate: Mapping[str, Any]) -> str:
    value = rate.get("value_percent")
    rendered = "N/A" if value is None else f"{float(value):.2f}%"
    return (
        f"{rendered} ({int(rate.get('numerator') or 0)}/"
        f"{int(rate.get('denominator') or 0)}, "
        f"excluded={int(rate.get('excluded') or 0)})"
    )


def render_agent_evaluation_text(report: Mapping[str, Any]) -> str:
    scenarios = report["controlled_scenarios"]
    summary = scenarios["summary"]
    metrics = report["runtime_observability"]["metrics"]
    lines = [
        "Paper Hunter Agent Evaluation Report",
        "====================================",
        "",
        f"Project Version: {report['project_version']}",
        f"Generated At: {report['generated_at']}",
        f"Overall Status: {str(report['overall_status']).upper()}",
        "",
        "Controlled Scenario Evaluation",
        "------------------------------",
        f"Total: {summary['total']}",
        f"Passed: {summary['passed']}",
        f"Failed: {summary['failed']}",
        f"Skipped: {summary['skipped']}",
        f"Scenario Pass Rate: {_format_rate(summary['pass_rate'])}",
        "",
    ]
    for result in scenarios["results"]:
        lines.append(
            f"[{str(result['status']).upper()}] {result['scenario_id']} "
            f"{result['title']} ({result.get('duration_ms')} ms)"
        )
        if result.get("error"):
            lines.append(f"  Error: {result['error']}")

    lines.extend(["", "Runtime Observability", "---------------------"])
    scope = metrics.get("scope", {})
    lines.append(
        "Samples: "
        f"tasks={scope.get('task_count', 0)}, "
        f"threads={scope.get('thread_count', 0)}, "
        f"traces={scope.get('trace_count', 0)}"
    )
    rate_titles = (
        ("task_completion_rate", "Task Completion Rate"),
        ("tool_success_rate", "Tool Success Rate"),
        ("approval_compliance_rate", "Approval Compliance Rate"),
        ("checkpoint_recovery_rate", "Checkpoint Recovery Rate"),
        ("fallback_success_rate", "Fallback Success Rate"),
    )
    rates = metrics.get("rates", {})
    for key, title in rate_titles:
        rate = rates.get(key, {})
        lines.append(f"{title}: {_format_rate(rate)}")
        if rate.get("definition"):
            lines.append(f"  Definition: {rate['definition']}")

    latency = metrics.get("latency", {})
    for key, title in (
        ("average_task_latency", "Average Task Latency"),
        ("average_node_latency", "Average Node Latency"),
    ):
        item = latency.get(key, {})
        value = item.get("value_ms")
        rendered = "N/A" if value is None else f"{float(value):.2f} ms"
        lines.append(
            f"{title}: {rendered} (samples={int(item.get('sample_count') or 0)})"
        )

    lines.extend(["", "Failure Distribution", "--------------------"])
    failed = metrics.get("failures", {}).get("failed_by_node", [])
    if failed:
        lines.extend(
            f"{item['node_name']}: {item['count']}" for item in failed
        )
    else:
        lines.append("No failed node traces in the current runtime sample.")

    lines.extend(["", "Limitations", "-----------"])
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def save_agent_evaluation_report(
    report: Mapping[str, Any],
    *,
    output_dir: Path = AGENT_EVALUATION_OUTPUT_DIR,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.fromisoformat(str(report["generated_at"]))
    timestamp = generated_at.strftime("%Y%m%d_%H%M%S")
    json_content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    text_content = render_agent_evaluation_text(report)
    paths = {
        "json": output_dir / f"agent_evaluation_{timestamp}.json",
        "text": output_dir / f"agent_evaluation_{timestamp}.txt",
        "latest_json": output_dir / "agent_evaluation_latest.json",
        "latest_text": output_dir / "agent_evaluation_latest.txt",
    }
    _write_atomic(paths["json"], json_content)
    _write_atomic(paths["text"], text_content)
    _write_atomic(paths["latest_json"], json_content)
    _write_atomic(paths["latest_text"], text_content)
    return paths

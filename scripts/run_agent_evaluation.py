import argparse
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from my_agent_project.agent_evaluation_service import (  # noqa: E402
    AGENT_EVALUATION_OUTPUT_DIR,
    build_agent_evaluation_report,
    parse_junit_scenario_results,
    save_agent_evaluation_report,
    scenario_test_nodeids,
)
from my_agent_project.agent_metrics_service import build_agent_metrics  # noqa: E402
from my_agent_project.config import configure_utf8_stdio  # noqa: E402


def _project_version() -> str:
    with (BASE_DIR / "pyproject.toml").open("rb") as file:
        payload = tomllib.load(file)
    return str(payload["project"]["version"])


def _run_scenarios(junit_path: Path) -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        *scenario_test_nodeids(),
        "-q",
        f"--junitxml={junit_path}",
    ]
    completed = subprocess.run(command, cwd=BASE_DIR, check=False)
    return int(completed.returncode)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run frozen Agent scenarios and generate JSON/text reports."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=AGENT_EVALUATION_OUTPUT_DIR,
        help="Report output directory (default: outputs/agent_evaluation).",
    )
    parser.add_argument(
        "--skip-runtime-metrics",
        action="store_true",
        help="Do not read the local runtime SQLite; emit an empty metrics sample.",
    )
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="paper-hunter-agent-eval-") as temp_dir:
        junit_path = Path(temp_dir) / "agent-scenarios.xml"
        pytest_exit_code = _run_scenarios(junit_path)
        scenario_results = parse_junit_scenario_results(junit_path)

    runtime_metrics = (
        build_agent_metrics(tasks=[], traces=[])
        if arguments.skip_runtime_metrics
        else build_agent_metrics()
    )
    report = build_agent_evaluation_report(
        scenario_results,
        runtime_metrics,
        project_version=_project_version(),
        pytest_exit_code=pytest_exit_code,
    )
    paths = save_agent_evaluation_report(
        report,
        output_dir=arguments.output_dir.resolve(),
    )
    print(
        "Agent evaluation: "
        f"{report['controlled_scenarios']['summary']['passed']}/"
        f"{report['controlled_scenarios']['summary']['total']} passed"
    )
    print(f"JSON report: {paths['latest_json']}")
    print(f"Text report: {paths['latest_text']}")
    return 0 if report["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

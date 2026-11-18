from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from my_agent_project.config import DEEPSEEK_MODEL
from my_agent_project.generation_evaluation_service import (
    evaluate_answer_against_reference,
)
from my_agent_project.model_service import ask_deepseek
from my_agent_project.research_conversation_service import (
    RESEARCH_FOLLOWUP_SYSTEM_PROMPT,
    build_no_followup_evidence_answer,
    build_research_followup_prompt,
    normalize_research_followup_citations,
)
from my_agent_project.research_loop_service import run_coverage_research_loop
from my_agent_project.research_report_service import (
    validate_research_report_citations,
)


DEFAULT_CASES = BASE_DIR / "benchmarks" / "research_chat_e2e_v1.json"
DEFAULT_OUTPUT = BASE_DIR / "outputs" / "research_chat_e2e_v1.json"
DEFAULT_REPORT = BASE_DIR / "docs" / "END_TO_END_EVALUATION.md"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _selected_cases(
    benchmark: Mapping[str, Any],
    case_ids: Sequence[str],
    limit: int | None,
) -> list[dict[str, Any]]:
    cases = [dict(item) for item in benchmark.get("cases", [])]
    if case_ids:
        requested = set(case_ids)
        cases = [item for item in cases if str(item.get("case_id")) in requested]
        missing = requested - {str(item.get("case_id")) for item in cases}
        if missing:
            raise ValueError(f"unknown case ids: {', '.join(sorted(missing))}")
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")
        cases = cases[:limit]
    if not cases:
        raise ValueError("no evaluation cases selected")
    return cases


def _evidence_covers_target(
    evidence: Mapping[str, Any],
    target_chunk_id: int,
) -> bool:
    chunk_ids = {int(evidence.get("chunk_id") or 0)}
    for key in ("seed_chunk_ids", "source_child_chunk_ids"):
        values = evidence.get(key)
        if isinstance(values, list):
            for value in values:
                try:
                    chunk_ids.add(int(value))
                except (TypeError, ValueError):
                    continue
    return target_chunk_id in chunk_ids


def _evidence_summary(evidence: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": str(item.get("evidence_id") or ""),
            "paper_id": int(item.get("paper_id") or 0),
            "paper_title": str(item.get("paper_title") or ""),
            "chunk_id": int(item.get("chunk_id") or 0),
            "parent_chunk_id": item.get("parent_chunk_id"),
            "seed_chunk_ids": list(item.get("seed_chunk_ids") or []),
            "section_title": str(item.get("section_title") or ""),
            "page_number": int(item.get("page_number") or 0),
            "content_preview": " ".join(
                str(item.get("content") or "").split()
            )[:320],
        }
        for item in evidence
    ]


def _action_trace(diagnostics: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = [dict(item) for item in diagnostics.get("actions", [])]
    completion = diagnostics.get("completion_action")
    if isinstance(completion, Mapping):
        actions.append(dict(completion))
    return [
        {
            "action_index": index,
            "tool_name": str(action.get("tool_name") or ""),
            "target_requirement_id": action.get("target_requirement_id"),
            "arguments": dict(action.get("arguments") or {}),
            "reason": str(action.get("reason") or ""),
            "tool_call_id": action.get("tool_call_id"),
        }
        for index, action in enumerate(actions, start=1)
    ]


def _badcase_stages(
    *,
    termination_status: str,
    target_hit_count: int,
    target_count: int,
    unresolved_core_count: int,
    quality: Mapping[str, float],
    citation_validation: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> list[str]:
    stages: list[str] = []
    if termination_status == "FAILED":
        stages.append("loop_execution")
    elif termination_status == "EXHAUSTED":
        stages.append("loop_exhausted")
    if target_hit_count < target_count:
        stages.append("evidence_retrieval_or_selection")
    if unresolved_core_count:
        stages.append("coverage_gap")
    if float(quality.get("faithfulness", 0.0)) < float(
        thresholds["faithfulness"]
    ):
        stages.append("answer_faithfulness")
    if (
        float(quality.get("answer_relevance", 0.0))
        < float(thresholds["answer_relevance"])
        or float(quality.get("reference_correctness", 0.0))
        < float(thresholds["reference_correctness"])
    ):
        stages.append("answer_completeness")
    if (
        citation_validation.get("status") != "passed"
        or float(quality.get("citation_correctness", 0.0))
        < float(thresholds["citation_correctness"])
    ):
        stages.append("citation")
    return stages


def evaluate_case(
    case: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float],
    max_actions: int,
    max_runtime: float,
    cache_stats: dict[str, int],
) -> dict[str, Any]:
    started_at = time.perf_counter()
    loop_result = run_coverage_research_loop(
        str(case["question"]),
        [int(case["paper_id"])],
        max_actions=max_actions,
        max_runtime=max_runtime,
    )
    evidence = [dict(item) for item in loop_result.evidence]
    if loop_result.quality_gate.get("model_call_allowed"):
        prompt = build_research_followup_prompt(
            str(case["question"]),
            "",
            evidence,
            loop_result.access_limitations,
        )
        answer, answer_model = ask_deepseek(
            RESEARCH_FOLLOWUP_SYSTEM_PROMPT,
            prompt,
        )
        answer = normalize_research_followup_citations(answer, evidence)
    else:
        answer = build_no_followup_evidence_answer(
            str(case["question"]),
            loop_result.access_limitations,
        )
        answer_model = "deterministic-no-evidence"

    citation_validation = validate_research_report_citations(answer, evidence)
    if evidence:
        quality = evaluate_answer_against_reference(
            question=str(case["question"]),
            reference_answer=str(case["reference_answer"]),
            answer=answer,
            evidence=evidence,
            cache_stats=cache_stats,
        )
    else:
        quality = {
            "claims": [],
            "faithfulness": 0.0,
            "answer_relevance": 0.0,
            "reference_correctness": 0.0,
            "citation_correctness": 0.0,
            "explicit_citation_pages": [],
            "notes": "No final Original Paper Evidence was available.",
        }

    target_ids = [int(value) for value in case.get("target_chunk_ids", [])]
    target_hits = [
        target_id
        for target_id in target_ids
        if any(_evidence_covers_target(item, target_id) for item in evidence)
    ]
    requirements = [item.as_dict() for item in loop_result.requirements]
    unresolved_core = [
        item
        for item in requirements
        if item.get("priority") == "core"
        and item.get("status") not in {"SUPPORTED", "NOT_AVAILABLE"}
    ]
    termination_status = str(loop_result.diagnostics.get("status") or "FAILED")
    stages = _badcase_stages(
        termination_status=termination_status,
        target_hit_count=len(target_hits),
        target_count=len(target_ids),
        unresolved_core_count=len(unresolved_core),
        quality=quality,
        citation_validation=citation_validation,
        thresholds=thresholds,
    )
    return {
        "case_id": str(case["case_id"]),
        "paper_id": int(case["paper_id"]),
        "paper_title": str(case["paper_title"]),
        "question_type": str(case["question_type"]),
        "question": str(case["question"]),
        "reference_answer": str(case["reference_answer"]),
        "termination_status": termination_status,
        "exhaustion_reason": loop_result.diagnostics.get("exhaustion_reason"),
        "action_count": int(loop_result.diagnostics.get("action_count") or 0),
        "agent_elapsed_seconds": round(
            float(loop_result.diagnostics.get("elapsed_seconds") or 0.0), 3
        ),
        "total_seconds": round(time.perf_counter() - started_at, 3),
        "actions": _action_trace(loop_result.diagnostics),
        "progress_events": list(
            loop_result.diagnostics.get("progress_events") or []
        ),
        "stall_count": int(loop_result.diagnostics.get("stall_count") or 0),
        "requirements": requirements,
        "unresolved_core_requirements": unresolved_core,
        "target_chunk_ids": target_ids,
        "target_hit_chunk_ids": target_hits,
        "target_coverage": round(len(target_hits) / len(target_ids), 4)
        if target_ids
        else 1.0,
        "evidence": _evidence_summary(evidence),
        "evidence_count": len(evidence),
        "answer": answer,
        "answer_model": answer_model,
        "quality": quality,
        "citation_validation": citation_validation,
        "answer_complete": (
            float(quality.get("answer_relevance", 0.0))
            >= float(thresholds["answer_relevance"])
            and float(quality.get("reference_correctness", 0.0))
            >= float(thresholds["reference_correctness"])
        ),
        "citation_correct": (
            citation_validation.get("status") == "passed"
            and float(quality.get("citation_correctness", 0.0))
            >= float(thresholds["citation_correctness"])
        ),
        "badcase_stages": stages,
        "passed": not stages,
    }


def _aggregate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if not item.get("error")]
    tool_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    termination_counts: Counter[str] = Counter()
    for item in results:
        termination_counts[str(item.get("termination_status") or "unknown")] += 1
        stage_counts.update(str(stage) for stage in item.get("badcase_stages", []))
    for item in completed:
        tool_counts.update(
            str(action.get("tool_name") or "")
            for action in item.get("actions", [])
            if action.get("tool_name") != "complete_research"
        )

    def average(field: str) -> float:
        values = [float(item.get("quality", {}).get(field, 0.0)) for item in completed]
        return round(sum(values) / len(values), 4) if values else 0.0

    target_count = sum(len(item.get("target_chunk_ids", [])) for item in results)
    target_hits = sum(
        len(item.get("target_hit_chunk_ids", [])) for item in results
    )
    return {
        "case_count": len(results),
        "completed_case_count": len(completed),
        "execution_error_count": len(results) - len(completed),
        "passed_case_count": sum(bool(item.get("passed")) for item in completed),
        "answer_complete_count": sum(
            bool(item.get("answer_complete")) for item in completed
        ),
        "citation_correct_count": sum(
            bool(item.get("citation_correct")) for item in completed
        ),
        "termination_counts": dict(sorted(termination_counts.items())),
        "tool_call_counts": dict(sorted(tool_counts.items())),
        "badcase_stage_counts": dict(sorted(stage_counts.items())),
        "target_chunk_coverage": round(target_hits / target_count, 4)
        if target_count
        else 0.0,
        "target_chunk_hits": target_hits,
        "target_chunk_count": target_count,
        "faithfulness": average("faithfulness"),
        "answer_relevance": average("answer_relevance"),
        "reference_correctness": average("reference_correctness"),
        "citation_correctness": average("citation_correctness"),
        "average_action_count": round(
            sum(int(item.get("action_count") or 0) for item in completed)
            / len(completed),
            2,
        )
        if completed
        else 0.0,
        "average_total_seconds": round(
            sum(float(item.get("total_seconds") or 0.0) for item in completed)
            / len(completed),
            3,
        )
        if completed
        else 0.0,
    }


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _tool_path(result: Mapping[str, Any]) -> str:
    names = [str(item.get("tool_name") or "") for item in result.get("actions", [])]
    return " → ".join(names) or "—"


def render_report(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    results = list(payload["results"])
    lines = [
        "# Research Chat 端到端评测与 Badcase 报告",
        "",
        f"> 评测版本：`{payload['benchmark_version']}`",
        ">",
        f"> 运行时间：`{payload['run_at']}`",
        ">",
        f"> 模型：`{payload['model']}`",
        "",
        "## 1. 评测范围",
        "",
        "本评测复用冻结检索集中的真实论文与问题，执行完整生产链路：",
        "",
        "```text",
        "用户问题 → DeepSeek Function Calling → Research Tool",
        "→ Original Paper Evidence → Coverage Reflection → complete_research",
        "→ 最终回答 → 引用校验 → Answer Judge",
        "```",
        "",
        "每条案例记录 Tool 轨迹、最终 Evidence、Target Chunk 覆盖、回答完整性、",
        "事实忠实度和引用正确性。完整回答与证据摘要保存在本地原始 JSON，公开报告",
        "只保留可审阅的指标和 Badcase 原因。",
        "",
        "## 2. 汇总结果",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 案例数 | {summary['case_count']} |",
        f"| 正常完成评测 | {summary['completed_case_count']}/{summary['case_count']} |",
        f"| Loop 执行错误 | {summary['execution_error_count']} |",
        f"| 全合同通过 | {summary['passed_case_count']}/{summary['case_count']} |",
        f"| 回答完整 | {summary['answer_complete_count']}/{summary['case_count']} |",
        f"| 引用正确 | {summary['citation_correct_count']}/{summary['case_count']} |",
        f"| Target Chunk Coverage | {summary['target_chunk_hits']}/{summary['target_chunk_count']} ({_percent(summary['target_chunk_coverage'])}) |",
        f"| Faithfulness | {_percent(summary['faithfulness'])} |",
        f"| Answer Relevance | {_percent(summary['answer_relevance'])} |",
        f"| Reference Correctness | {_percent(summary['reference_correctness'])} |",
        f"| Citation Correctness | {_percent(summary['citation_correctness'])} |",
        f"| 平均 Tool Action | {summary['average_action_count']} |",
        f"| 平均总耗时 | {summary['average_total_seconds']} 秒 |",
        "",
        f"结束状态：`{json.dumps(summary['termination_counts'], ensure_ascii=False)}`",
        "",
        f"工具调用：`{json.dumps(summary['tool_call_counts'], ensure_ascii=False)}`",
        "",
        f"Badcase 阶段：`{json.dumps(summary['badcase_stage_counts'], ensure_ascii=False)}`",
        "",
        "## 3. 逐题结果",
        "",
        "| Case | 类型 | 结束 | Tool 路径 | Target | 完整性 | 引用 | Badcase |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in results:
        if item.get("error"):
            target = f"0/{len(item.get('target_chunk_ids', []))}"
            lines.append(
                f"| {item['case_id']} | {item.get('question_type', '—')} | FAILED | — | {target} | 未通过 | 未通过 | loop_execution |"
            )
            continue
        target = f"{len(item['target_hit_chunk_ids'])}/{len(item['target_chunk_ids'])}"
        badcase = ", ".join(item.get("badcase_stages", [])) or "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["case_id"]),
                    str(item["question_type"]),
                    str(item["termination_status"]),
                    _tool_path(item).replace("|", "\\|"),
                    target,
                    "通过" if item.get("answer_complete") else "未通过",
                    "通过" if item.get("citation_correct") else "未通过",
                    badcase,
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 4. Badcase 分析",
            "",
        ]
    )
    badcases = [item for item in results if item.get("badcase_stages") or item.get("error")]
    if not badcases:
        lines.append("本轮没有触发预定义 Badcase。")
    for item in badcases:
        lines.extend(
            [
                f"### {item['case_id']} — {item.get('question', '')}",
                "",
            ]
        )
        if item.get("error"):
            lines.append(f"- 执行错误：`{item['error']}`")
            lines.append("")
            continue
        unresolved = [
            f"{req.get('id')}: {req.get('gap')}"
            for req in item.get("unresolved_core_requirements", [])
        ]
        quality = item.get("quality", {})
        lines.extend(
            [
                f"- 失败阶段：`{', '.join(item['badcase_stages'])}`",
                f"- Tool 路径：`{_tool_path(item)}`",
                f"- 最终 Evidence：`{', '.join(e['evidence_id'] for e in item.get('evidence', [])) or 'none'}`",
                f"- Target：`{item['target_hit_chunk_ids']}/{item['target_chunk_ids']}`",
                f"- 未解决 Coverage：{'；'.join(unresolved) or '无'}",
                f"- Judge：faithfulness={quality.get('faithfulness', 0):.2f}, relevance={quality.get('answer_relevance', 0):.2f}, reference={quality.get('reference_correctness', 0):.2f}, citation={quality.get('citation_correctness', 0):.2f}",
                f"- 诊断备注：{quality.get('notes') or '无'}",
                "",
            ]
        )

    contract_errors = [
        item["case_id"] for item in results if item.get("error")
    ]
    equivalent_evidence = [
        item["case_id"]
        for item in results
        if not item.get("error")
        and float(item.get("target_coverage", 0.0)) < 1.0
        and item.get("answer_complete")
    ]
    incomplete_answers = [
        item["case_id"]
        for item in results
        if not item.get("error") and not item.get("answer_complete")
    ]
    citation_granularity = [
        item["case_id"]
        for item in results
        if not item.get("error")
        and item.get("citation_validation", {}).get("status") == "passed"
        and not item.get("citation_correct")
    ]
    lines.extend(
        [
            "## 5. 跨案例诊断",
            "",
            f"- Tool 参数合同错误：`{', '.join(contract_errors) or '无'}`。这类问题发生在业务 Tool 执行前，说明当前单次纠正只覆盖多 Tool Call，尚未覆盖参数中出现未声明字段的情况。",
            f"- 未命中精确 Target 但回答完整：`{', '.join(equivalent_evidence) or '无'}`。这说明 Agent 可能找到同页或同主题的等价证据，Exact Target 不能单独代表回答质量。",
            f"- 回答不完整：`{', '.join(incomplete_answers) or '无'}`。主要表现为缺少参考答案中的实验设置、推理过程或对比方案，需要结合最终 Evidence 判断是召回不足还是 Agent 过早完成。",
            f"- 页码合法但声明级引用未达阈值：`{', '.join(citation_granularity) or '无'}`。这类回答通常在段末集中引用多个页面，代码能确认页码来自 Evidence，但 Judge 认为部分原子主张没有紧邻引用。",
            "",
            "## 6. 评测边界",
            "",
            "- Answer Judge 与被评模型使用同一 DeepSeek 配置，分数用于同项目内诊断，不能视为人工金标准。",
            "- 引用采用两层检查：代码先验证标题和页码来自最终 Evidence，Judge 再判断事实与引用页是否匹配。",
            "- Target Chunk Exact Coverage 用于定位检索与选择损失，不等同于最终答案准确率。",
            "- 当前三篇冻结论文没有持久化 Parent Chunk，因此本轮无法验证真实 Parent 覆盖，只能按 Child ID 统计 Target。",
            "- 本报告不根据结果自动修改 RAG 或 Agent 参数；Badcase 只作为后续工程决策依据。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    benchmark = _load_json(args.cases)
    thresholds = {
        key: float(value)
        for key, value in dict(benchmark.get("thresholds") or {}).items()
    }
    required_thresholds = {
        "faithfulness",
        "answer_relevance",
        "reference_correctness",
        "citation_correctness",
    }
    if set(thresholds) != required_thresholds:
        raise ValueError("benchmark thresholds are incomplete")
    cases = _selected_cases(benchmark, args.case_id, args.limit)

    if args.dry_run:
        return {
            "benchmark_version": benchmark["version"],
            "selected_case_ids": [item["case_id"] for item in cases],
            "case_count": len(cases),
            "dry_run": True,
        }

    if args.report_only:
        if not args.output.exists():
            raise FileNotFoundError(args.output)
        payload = _load_json(args.output)
        case_map = {str(item["case_id"]): item for item in cases}
        for result in payload.get("results", []):
            case = case_map.get(str(result.get("case_id")))
            if case is None:
                continue
            result.setdefault(
                "target_chunk_ids",
                [int(value) for value in case.get("target_chunk_ids", [])],
            )
            result.setdefault("target_hit_chunk_ids", [])
            result.setdefault("target_coverage", 0.0)
            if result.get("error"):
                result.setdefault("termination_status", "FAILED")
                result.setdefault("action_count", 0)
                result.setdefault("answer_complete", False)
                result.setdefault("citation_correct", False)
        payload["summary"] = _aggregate(payload.get("results", []))
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(payload), encoding="utf-8")
        return payload

    existing: dict[str, dict[str, Any]] = {}
    if args.resume and args.output.exists():
        previous = _load_json(args.output)
        existing = {
            str(item["case_id"]): dict(item)
            for item in previous.get("results", [])
            if not item.get("error")
        }

    cache_stats: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    run_started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        case_id = str(case["case_id"])
        if case_id in existing:
            result = existing[case_id]
            print(f"[{index}/{len(cases)}] {case_id}: reused")
        else:
            print(f"[{index}/{len(cases)}] {case_id}: running", flush=True)
            try:
                result = evaluate_case(
                    case,
                    thresholds=thresholds,
                    max_actions=args.max_actions,
                    max_runtime=args.max_runtime,
                    cache_stats=cache_stats,
                )
                print(
                    f"[{index}/{len(cases)}] {case_id}: "
                    f"{result['termination_status']} "
                    f"actions={result['action_count']} "
                    f"badcases={result['badcase_stages']}",
                    flush=True,
                )
            except Exception as exc:
                result = {
                    "case_id": case_id,
                    "paper_id": int(case["paper_id"]),
                    "paper_title": str(case["paper_title"]),
                    "question_type": str(case["question_type"]),
                    "question": str(case["question"]),
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                    "termination_status": "FAILED",
                    "action_count": 0,
                    "target_chunk_ids": [
                        int(value) for value in case.get("target_chunk_ids", [])
                    ],
                    "target_hit_chunk_ids": [],
                    "target_coverage": 0.0,
                    "answer_complete": False,
                    "citation_correct": False,
                    "badcase_stages": ["loop_execution"],
                    "passed": False,
                }
                print(
                    f"[{index}/{len(cases)}] {case_id}: ERROR {result['error']}",
                    flush=True,
                )
        results.append(result)
        partial_payload = {
            "version": "research-chat-e2e-result-v1",
            "benchmark_version": benchmark["version"],
            "run_at": datetime.now(timezone.utc).isoformat(),
            "model": DEEPSEEK_MODEL,
            "thresholds": thresholds,
            "summary": _aggregate(results),
            "cache_stats": cache_stats,
            "results": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(partial_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    payload = _load_json(args.output)
    payload["total_duration_seconds"] = round(
        time.perf_counter() - run_started, 3
    )
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(payload), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the production Research Agent and evaluate final answers."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--max-runtime", type=float, default=90.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    parsed_args = parse_args()
    result_payload = run(parsed_args)
    print(json.dumps(result_payload.get("summary", result_payload), ensure_ascii=False, indent=2))

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from my_agent_project.model_service import ask_deepseek_json
from my_agent_project.rag_service import RERANK_SYSTEM_PROMPT, _rerank_prompt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "rerank_p2_cases.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "rerank_p2_results.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) != 15:
        raise ValueError("P2评测集必须恰好包含15个案例")
    return data


def evaluate_contract(response: Any, candidate_count: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(response, dict):
        return ["JSON顶层不是对象"]
    if set(response) != {"ranking"}:
        errors.append("JSON只能包含ranking字段")
    ranking = response.get("ranking")
    if not isinstance(ranking, list):
        return [*errors, "ranking不是数组"]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in ranking):
        errors.append("ranking包含非整数ID")
        return errors
    expected = set(range(1, candidate_count + 1))
    if len(ranking) != candidate_count:
        errors.append("ranking没有恰好包含全部Candidate ID")
    if len(set(ranking)) != len(ranking):
        errors.append("ranking包含重复ID")
    if set(ranking) - expected:
        errors.append("ranking生成了不存在的ID")
    if expected - set(ranking):
        errors.append("ranking遗漏了Candidate ID")
    return errors


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    candidates = [{"content": content} for content in case["candidates"]]
    user_prompt = _rerank_prompt(case["question"], candidates)
    try:
        response, model = ask_deepseek_json(
            RERANK_SYSTEM_PROMPT,
            user_prompt,
            max_tokens=512,
        )
    except Exception as exc:
        return {
            "id": case["id"],
            "category": case["category"],
            "question": case["question"],
            "expected_top1": case["expected_top1"],
            "relative_order": case.get("relative_order", []),
            "ranking": None,
            "response": None,
            "model": None,
            "status": "FAIL",
            "errors": [f"模型调用或JSON解析失败：{exc}"],
        }

    errors = evaluate_contract(response, len(candidates))
    ranking = response.get("ranking") if isinstance(response, dict) else None
    if not errors and ranking[0] != case["expected_top1"]:
        errors.append(
            f"Top1错误：期望{case['expected_top1']}，实际{ranking[0]}"
        )
    if not errors:
        positions = {candidate_id: index for index, candidate_id in enumerate(ranking)}
        for higher, lower in case.get("relative_order", []):
            if positions[higher] >= positions[lower]:
                errors.append(f"关键顺序错误：要求{higher}>{lower}")

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "expected_top1": case["expected_top1"],
        "relative_order": case.get("relative_order", []),
        "ranking": ranking,
        "response": response,
        "model": model,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the current P2 reranker prompt")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases)}] {case['id']}", flush=True)
        result = evaluate_case(case)
        print(
            f"  {result['status']} ranking={result['ranking']} "
            f"errors={result['errors']}",
            flush=True,
        )
        results.append(result)

    passed = sum(result["status"] == "PASS" for result in results)
    report = {
        "prompt": "RERANK_SYSTEM_PROMPT",
        "case_file": str(args.cases),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": passed / len(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({key: report[key] for key in ("total", "passed", "failed", "pass_rate")}, ensure_ascii=False))
    print(f"saved={args.output}")


if __name__ == "__main__":
    main()

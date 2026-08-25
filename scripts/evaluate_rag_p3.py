from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from my_agent_project.config import DEEPSEEK_MODEL, DEEPSEEK_THINKING
from my_agent_project.model_service import ask_deepseek
from my_agent_project.rag_service import RAG_SYSTEM_PROMPT, build_rag_prompt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "rag_p3_cases.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "rag_p3_results.json"
PAGE_PATTERN = re.compile(r"第\s*(\d+)\s*页|\bp\.?\s*(\d+)\b", re.IGNORECASE)


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) != 15:
        raise ValueError("P3评测集必须恰好包含15个案例")
    return cases


def cited_pages(text: str) -> set[int]:
    pages: set[int] = set()
    for match in PAGE_PATTERN.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            pages.add(int(value))
    return pages


def normalized_required_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def automatic_checks(case: dict[str, Any], answer: str) -> list[str]:
    errors: list[str] = []
    if not answer.strip():
        return ["回答为空"]
    if "证据来源" not in answer:
        errors.append("缺少证据来源")
    if case["paper_title"] not in answer:
        errors.append("回答或证据来源缺少论文标题")

    input_pages = {int(item["page_number"]) for item in case["evidence"]}
    actual_pages = cited_pages(answer)
    unexpected_pages = actual_pages - input_pages
    if unexpected_pages:
        errors.append(f"引用了输入中不存在的页码：{sorted(unexpected_pages)}")
    missing_pages = set(case["expected_pages"]) - actual_pages
    if missing_pages:
        errors.append(f"缺少预期使用页码：{sorted(missing_pages)}")
    unused_pages = actual_pages - set(case["expected_pages"])
    if unused_pages:
        errors.append(f"引用了不应作为答案来源的页码：{sorted(unused_pages)}")

    answer_folded = answer.casefold()
    answer_required = normalized_required_text(answer)
    for claim in case.get("forbidden_claims", []):
        if claim.casefold() in answer_folded:
            errors.append(f"出现禁止陈述：{claim}")
    for alternatives in case.get("required_term_groups", []):
        if not any(
            normalized_required_text(term) in answer_required
            for term in alternatives
        ):
            errors.append(f"缺少必要内容之一：{alternatives}")

    if case.get("insufficient") and "当前证据不足" not in answer:
        errors.append("证据不足案例未使用“当前证据不足”")
    if case.get("partial"):
        if "CIFAR-10" not in answer or "0.001" not in answer:
            errors.append("部分证据案例遗漏了可回答部分")
    return errors


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    paper = {
        "title": case["paper_title"],
        "page_url": "https://example.invalid/paper",
    }
    prompt = build_rag_prompt(case["question"], paper, case["evidence"])
    try:
        answer, model = ask_deepseek(RAG_SYSTEM_PROMPT, prompt)
    except Exception as exc:
        return {
            "case_id": case["case_id"],
            "type": case["type"],
            "question": case["question"],
            "answer": None,
            "model": None,
            "automatic_status": "FAIL",
            "automatic_errors": [f"模型调用失败：{exc}"],
        }
    errors = automatic_checks(case, answer)
    return {
        "case_id": case["case_id"],
        "type": case["type"],
        "question": case["question"],
        "answer": answer,
        "model": model,
        "automatic_status": "PASS" if not errors else "FAIL",
        "automatic_errors": errors,
        "cited_pages": sorted(cited_pages(answer)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the current P3 RAG prompt")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--recheck-existing",
        action="store_true",
        help="Re-run deterministic checks on saved answers without calling the model",
    )
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.recheck_existing:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        cases_by_id = {case["case_id"]: case for case in cases}
        for result in report["results"]:
            case = cases_by_id[result["case_id"]]
            errors = automatic_checks(case, result["answer"] or "")
            result["automatic_errors"] = errors
            result["automatic_status"] = "PASS" if not errors else "FAIL"
            result["cited_pages"] = sorted(cited_pages(result["answer"] or ""))
        passed = sum(
            item["automatic_status"] == "PASS" for item in report["results"]
        )
        report["automatic_passed"] = passed
        report["automatic_failed"] = len(report["results"]) - passed
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "total": report["total"],
                    "automatic_passed": report["automatic_passed"],
                    "automatic_failed": report["automatic_failed"],
                },
                ensure_ascii=False,
            )
        )
        return

    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases)}] {case['case_id']}", flush=True)
        result = evaluate_case(case)
        print(
            f"  {result['automatic_status']} pages={result.get('cited_pages')} "
            f"errors={result['automatic_errors']}",
            flush=True,
        )
        results.append(result)

    passed = sum(item["automatic_status"] == "PASS" for item in results)
    report = {
        "prompt": "RAG_SYSTEM_PROMPT",
        "case_file": str(args.cases),
        "model": DEEPSEEK_MODEL,
        "thinking": DEEPSEEK_THINKING,
        "temperature": "not explicitly set; provider default",
        "total": len(results),
        "automatic_passed": passed,
        "automatic_failed": len(results) - passed,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "total": report["total"],
                "automatic_passed": report["automatic_passed"],
                "automatic_failed": report["automatic_failed"],
            },
            ensure_ascii=False,
        )
    )
    print(f"saved={args.output}")


if __name__ == "__main__":
    main()

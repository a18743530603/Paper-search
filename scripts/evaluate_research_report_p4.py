from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from my_agent_project.config import DEEPSEEK_MODEL, DEEPSEEK_THINKING
from my_agent_project.model_service import ask_deepseek
from my_agent_project.research_report_service import (
    RESEARCH_REPORT_SYSTEM_PROMPT,
    build_research_report_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "research_report_p4_cases.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "research_report_p4_results.json"
MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^\s*#{1,6}\s+")
PLAIN_HEADING_PATTERN = re.compile(r"(?m)^\s*【([^】\r\n]+)】\s*$")
BRACKET_PATTERN = re.compile(r"\[([^\[\]]+)\]")
CHINESE_CITATION_ITEM_PATTERN = re.compile(
    r"^\s*(.+?)，\s*第\s*(\d+)\s*页\s*$"
)


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in value if character.isalnum())


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) != 15:
        raise ValueError("P4评测集必须恰好包含15个案例")
    return cases


def service_inputs(context: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    evidence = [
        {
            "paper_id": item["paper_id"],
            "paper_title": item["paper_title"],
            "page_number": item["physical_page"],
            "chunk_id": item["chunk_id"],
            "content": item["excerpt"],
        }
        for item in context["evidence"]
    ]
    limitations = [
        {
            "paper_id": item["paper_id"],
            "paper_title": item["paper_title"],
            "reason": item["reason"],
            "url": item["legal_access_url"],
        }
        for item in context["access_limitations"]
    ]
    return evidence, limitations


def parse_citations(answer: str) -> tuple[list[tuple[str, int]], list[str]]:
    citations: list[tuple[str, int]] = []
    malformed: list[str] = []
    for bracketed_text in BRACKET_PATTERN.findall(answer):
        looks_like_citation = (
            ("第" in bracketed_text and "页" in bracketed_text)
            or bool(re.search(r",\s*p\.\s*\d+", bracketed_text, re.IGNORECASE))
        )
        if not looks_like_citation:
            continue
        parsed_items: list[tuple[str, int]] = []
        for item in bracketed_text.split("；"):
            match = CHINESE_CITATION_ITEM_PATTERN.fullmatch(item)
            if match is None:
                malformed.append(f"[{bracketed_text}]")
                parsed_items = []
                break
            parsed_items.append((match.group(1).strip(), int(match.group(2))))
        citations.extend(parsed_items)
    return citations, list(dict.fromkeys(malformed))


def markdown_table_lines(answer: str) -> list[str]:
    return [
        line.strip()
        for line in answer.splitlines()
        if line.strip().startswith("|") and line.strip().count("|") >= 2
    ]


def empty_plain_sections(answer: str) -> list[str]:
    matches = list(PLAIN_HEADING_PATTERN.finditer(answer))
    empty: list[str] = []
    for index, match in enumerate(matches):
        content_start = match.end()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        if not answer[content_start:content_end].strip():
            empty.append(match.group(1).strip())
    return empty


def automatic_checks(case: dict[str, Any], answer: str) -> list[str]:
    errors: list[str] = []
    if not answer.strip():
        return ["报告为空"]

    if MARKDOWN_HEADING_PATTERN.search(answer):
        errors.append("出现# / ## / ###等Markdown标题")
    table_lines = markdown_table_lines(answer)
    if table_lines:
        errors.append(f"出现Markdown表格：{table_lines[:3]}")
    if "```" in answer:
        errors.append("出现Markdown代码块")
    empty_sections = empty_plain_sections(answer)
    if empty_sections:
        errors.append(f"出现无内容纯文本章节：{empty_sections}")

    evidence_pairs = {
        (item["paper_title"], int(item["physical_page"]))
        for item in case["research_context"]["evidence"]
    }
    citations, malformed_citations = parse_citations(answer)
    if malformed_citations:
        errors.append(f"引用未使用当前中文格式：{malformed_citations}")
    invalid = sorted(set(citations) - evidence_pairs)
    if invalid:
        errors.append(f"出现输入中不存在或跨论文错配的引用：{invalid}")
    missing = sorted(
        set((title, int(page)) for title, page in case["expected_citations"])
        - set(citations)
    )
    if missing:
        errors.append(f"缺少预期引用：{missing}")

    answer_folded = answer.casefold()
    answer_normalized = normalized(answer)
    for alternatives in case.get("required_term_groups", []):
        if not any(normalized(term) in answer_normalized for term in alternatives):
            errors.append(f"缺少必要内容之一：{alternatives}")
    for claim in case.get("forbidden_claims", []):
        if claim.casefold() in answer_folded:
            errors.append(f"出现禁止陈述：{claim}")
    for url in case.get("expected_urls", []):
        if url not in answer:
            errors.append(f"缺少合法访问URL：{url}")
    return errors


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    context = case["research_context"]
    evidence, limitations = service_inputs(context)
    prompt = build_research_report_prompt(
        context["research_question"],
        evidence,
        limitations,
    )
    try:
        answer, model = ask_deepseek(RESEARCH_REPORT_SYSTEM_PROMPT, prompt)
    except Exception as exc:
        return {
            "case_id": case["case_id"],
            "type": case["type"],
            "answer": None,
            "model": None,
            "automatic_status": "FAIL",
            "automatic_errors": [f"模型调用失败：{exc}"],
            "citations": [],
        }
    errors = automatic_checks(case, answer)
    return {
        "case_id": case["case_id"],
        "type": case["type"],
        "answer": answer,
        "model": model,
        "automatic_status": "PASS" if not errors else "FAIL",
        "automatic_errors": errors,
        "citations": parse_citations(answer)[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the current P4 research report prompt")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--recheck-existing",
        action="store_true",
        help="Re-run deterministic checks on saved reports without calling the model",
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
            result["citations"] = parse_citations(result["answer"] or "")[0]
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
            f"  {result['automatic_status']} citations={result['citations']} "
            f"errors={result['automatic_errors']}",
            flush=True,
        )
        results.append(result)

    passed = sum(item["automatic_status"] == "PASS" for item in results)
    report = {
        "prompt": "RESEARCH_REPORT_SYSTEM_PROMPT",
        "case_file": str(args.cases),
        "model": DEEPSEEK_MODEL,
        "thinking": DEEPSEEK_THINKING,
        "temperature": "not explicitly set; provider default",
        "max_tokens": "not explicitly set; provider default",
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

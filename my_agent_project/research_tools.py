from collections import Counter
from dataclasses import asdict
from time import perf_counter
from typing import Any

from langchain_core.tools import tool

from .db import get_document, get_paper
from .paper_workflow import PREPARATION_READY, prepare_paper_for_qa
from .rag_service import paper_access_url, retrieve_relevant_chunks
from .schemas import STATUS_FAILED
from .search_service import search_all


LITERATURE_SEARCH_TOOL_NAME = "search_literature"
PREPARE_PAPER_TOOL_NAME = "prepare_paper_for_research"
RETRIEVE_EVIDENCE_TOOL_NAME = "retrieve_paper_evidence"


@tool(LITERATURE_SEARCH_TOOL_NAME)
def search_literature(query: str, max_results: int) -> dict[str, Any]:
    """Search arXiv and Crossref for academic paper metadata."""
    query = " ".join(query.split())
    if not query:
        raise ValueError("检索词不能为空")
    if len(query) > 240:
        raise ValueError("检索词不能超过240个字符")
    if not 1 <= max_results <= 50:
        raise ValueError("max_results必须在1到50之间")

    started = perf_counter()
    candidates = search_all(query, max_results)
    source_counts = Counter(
        paper.source for paper in candidates if paper.source != "system"
    )
    failures = [paper for paper in candidates if paper.status == STATUS_FAILED]
    healthy_count = len(candidates) - len(failures)
    if failures and healthy_count:
        status = "partial"
    elif failures:
        status = "failed"
    else:
        status = "succeeded"
    error = "; ".join(paper.summary for paper in failures)[:500]

    return {
        "tool_name": LITERATURE_SEARCH_TOOL_NAME,
        "input": {"query": query, "max_results": max_results},
        "status": status,
        "result_count": healthy_count,
        "source_counts": dict(sorted(source_counts.items())),
        "error": error,
        "duration_ms": max(0, round((perf_counter() - started) * 1000)),
        "candidates": [asdict(paper) for paper in candidates],
    }


@tool(PREPARE_PAPER_TOOL_NAME)
def prepare_paper_for_research(paper_id: int) -> dict[str, Any]:
    """Download, parse, and index one user-approved paper for research."""
    if paper_id < 1:
        raise ValueError("paper_id必须为正整数")
    started = perf_counter()
    paper = get_paper(paper_id)
    if paper is None:
        raise LookupError("论文记录不存在")

    preparation_status = prepare_paper_for_qa(paper_id)
    document = get_document(paper_id)
    error = ""
    if preparation_status != PREPARATION_READY:
        error = str(
            (document["index_error"] if document else None)
            or (document["error"] if document else None)
            or paper["error"]
            or "论文未完成全文准备"
        )[:500]
    return {
        "tool_name": PREPARE_PAPER_TOOL_NAME,
        "input": {"paper_id": paper_id},
        "status": (
            "succeeded" if preparation_status == PREPARATION_READY else "failed"
        ),
        "preparation_status": preparation_status or "failed",
        "error": error,
        "duration_ms": max(0, round((perf_counter() - started) * 1000)),
    }


@tool(RETRIEVE_EVIDENCE_TOOL_NAME)
def retrieve_paper_evidence(
    paper_id: int,
    question: str,
    top_k: int = 3,
) -> dict[str, Any]:
    """Retrieve page-numbered evidence from one indexed academic paper."""
    question = " ".join(question.split())
    if paper_id < 1:
        raise ValueError("paper_id必须为正整数")
    if not question:
        raise ValueError("研究问题不能为空")
    if not 1 <= top_k <= 6:
        raise ValueError("top_k必须在1到6之间")
    paper = get_paper(paper_id)
    if paper is None:
        raise LookupError("论文记录不存在")

    started = perf_counter()
    evidence = retrieve_relevant_chunks(paper_id, question, top_k=top_k)
    return {
        "tool_name": RETRIEVE_EVIDENCE_TOOL_NAME,
        "input": {"paper_id": paper_id, "question": question, "top_k": top_k},
        "status": "succeeded" if evidence else "failed",
        "result_count": len(evidence),
        "paper_title": paper["title"],
        "page_url": paper_access_url(dict(paper)),
        "error": "" if evidence else "没有检索到相关全文证据",
        "duration_ms": max(0, round((perf_counter() - started) * 1000)),
        "evidence": evidence,
    }


RESEARCH_TOOL_REGISTRY = {
    search_literature.name: search_literature,
    prepare_paper_for_research.name: prepare_paper_for_research,
    retrieve_paper_evidence.name: retrieve_paper_evidence,
}


def invoke_research_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    selected_tool = RESEARCH_TOOL_REGISTRY.get(name)
    if selected_tool is None:
        raise ValueError(f"未注册的研究工具: {name}")
    result = selected_tool.invoke(arguments)
    if not isinstance(result, dict):
        raise TypeError(f"研究工具{name}返回了无效结果")
    return result

from collections import Counter
from dataclasses import asdict
from time import perf_counter
from typing import Any

from langchain_core.tools import tool

from .config import (
    RESEARCH_CANDIDATE_POOL_SIZE,
    RESEARCH_FINAL_SEED_COUNT,
    RESEARCH_MMR_ENABLED,
    RESEARCH_MMR_LAMBDA,
    RESEARCH_PARENT_EXPANSION_ENABLED,
    RESEARCH_PRF_ENABLED,
    RESEARCH_RERANKER_ENABLED,
    RESEARCH_RERANKER_MODEL,
)
from .db import get_document, get_paper
from .paper_search_mcp_service import LEGACY_SOURCE_IDS, normalize_source_ids
from .paper_workflow import PREPARATION_READY, prepare_paper_for_qa
from .rag_service import paper_access_url, retrieve_relevant_chunks
from .schemas import STATUS_FAILED
from .search_service import search_all, search_many


LITERATURE_SEARCH_TOOL_NAME = "search_literature"
PREPARE_PAPER_TOOL_NAME = "prepare_paper_for_research"
RETRIEVE_EVIDENCE_TOOL_NAME = "retrieve_paper_evidence"


@tool(LITERATURE_SEARCH_TOOL_NAME)
def search_literature(query: str, max_results: int) -> dict[str, Any]:
    """Search the configured academic sources for paper metadata."""
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


def search_literature_batch(
    queries: list[str],
    *,
    sources: list[str] | tuple[str, ...] = LEGACY_SOURCE_IDS,
    max_results: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected_sources = normalize_source_ids(sources)
    batches = search_many(
        queries,
        sources=selected_sources,
        max_results=max_results,
    )
    candidates: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for batch in batches:
        source_counts = Counter(paper.source for paper in batch.candidates)
        error = "; ".join(
            f"{source}: {message}"
            for source, message in sorted(batch.errors.items())
        )[:500]
        if batch.errors and batch.candidates:
            status = "partial"
        elif batch.errors:
            status = "failed"
        else:
            status = "succeeded"
        candidates.extend(asdict(paper) for paper in batch.candidates)
        tool_calls.append(
            {
                "tool_name": LITERATURE_SEARCH_TOOL_NAME,
                "input": {
                    "query": batch.query,
                    "max_results": max_results,
                    "sources": list(selected_sources),
                },
                "status": status,
                "result_count": len(batch.candidates),
                "source_counts": dict(sorted(source_counts.items())),
                "sources_used": list(batch.sources_used),
                "source_results": dict(sorted(batch.source_counts.items())),
                "raw_total": batch.raw_total,
                "total": batch.total,
                "error": error,
                "duration_ms": batch.duration_ms,
            }
        )
    return candidates, tool_calls


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


def retrieve_paper_evidence_with_lexical_query(
    paper_id: int,
    question: str,
    *,
    lexical_query: str,
    top_k: int = RESEARCH_FINAL_SEED_COUNT,
) -> dict[str, Any]:
    question = " ".join(question.split())
    if paper_id < 1:
        raise ValueError("paper_id必须为正整数")
    if not question:
        raise ValueError("研究问题不能为空")
    lexical_query = " ".join(str(lexical_query or question).split())
    if not lexical_query:
        lexical_query = question
    if not 1 <= top_k <= 6:
        raise ValueError("top_k必须在1到6之间")
    paper = get_paper(paper_id)
    if paper is None:
        raise LookupError("论文记录不存在")

    started = perf_counter()
    retrieval_diagnostics: dict[str, Any] = {}
    evidence = retrieve_relevant_chunks(
        paper_id,
        question,
        lexical_query=lexical_query,
        top_k=top_k,
        prf_enabled=RESEARCH_PRF_ENABLED and lexical_query != question,
        candidate_pool_size=RESEARCH_CANDIDATE_POOL_SIZE,
        reranker_enabled=RESEARCH_RERANKER_ENABLED,
        reranker_model=RESEARCH_RERANKER_MODEL,
        mmr_enabled=RESEARCH_MMR_ENABLED,
        mmr_lambda=RESEARCH_MMR_LAMBDA,
        parent_expansion_enabled=RESEARCH_PARENT_EXPANSION_ENABLED,
        evidence_paper_title=str(paper["title"]),
        retrieval_diagnostics=retrieval_diagnostics,
    )
    return {
        "tool_name": RETRIEVE_EVIDENCE_TOOL_NAME,
        "input": {
            "paper_id": paper_id,
            "question": question,
            "lexical_query": lexical_query,
            "semantic_query_type": "original_query",
            "tfidf_query_type": (
                "english_retrieval_query"
                if lexical_query != question
                else "original_query"
            ),
            "top_k": top_k,
            "candidate_pool_size": RESEARCH_CANDIDATE_POOL_SIZE,
            "reranker_enabled": RESEARCH_RERANKER_ENABLED,
            "reranker_model": RESEARCH_RERANKER_MODEL,
            "mmr_enabled": RESEARCH_MMR_ENABLED,
            "mmr_lambda": RESEARCH_MMR_LAMBDA,
            "parent_expansion_enabled": RESEARCH_PARENT_EXPANSION_ENABLED,
        },
        "status": "succeeded" if evidence else "failed",
        "result_count": len(evidence),
        "paper_title": paper["title"],
        "page_url": paper_access_url(dict(paper)),
        "error": "" if evidence else "没有检索到相关全文证据",
        "duration_ms": max(0, round((perf_counter() - started) * 1000)),
        "diagnostics": retrieval_diagnostics,
        "evidence": evidence,
    }


@tool(RETRIEVE_EVIDENCE_TOOL_NAME)
def retrieve_paper_evidence(
    paper_id: int,
    question: str,
    top_k: int = RESEARCH_FINAL_SEED_COUNT,
) -> dict[str, Any]:
    """Retrieve page-numbered evidence from one indexed academic paper."""
    return retrieve_paper_evidence_with_lexical_query(
        paper_id,
        question,
        lexical_query=question,
        top_k=top_k,
    )


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

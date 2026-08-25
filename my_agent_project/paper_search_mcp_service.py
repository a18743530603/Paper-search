from __future__ import annotations

import ast
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from time import perf_counter
from typing import Any, AsyncIterator, Iterable, Sequence
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .download_service import can_attempt_download
from .schemas import PaperCandidate, STATUS_AVAILABLE, STATUS_LINK_ONLY


SEARCH_PAPERS_TOOL_NAME = "search_papers"
DEFAULT_MAX_RESULTS_PER_SOURCE = 3
DEFAULT_MCP_READ_TIMEOUT_SECONDS = 120
DEFAULT_MCP_SERVER_COMMAND = "uvx"
DEFAULT_MCP_SERVER_ARGS = (
    "--from",
    "paper-search-mcp==0.1.4",
    "paper-search-mcp",
)

SEARCH_SOURCE_OPTIONS = (
    ("arxiv", "arXiv"),
    ("pubmed", "PubMed"),
    ("biorxiv", "bioRxiv"),
    ("medrxiv", "medRxiv"),
    ("google_scholar", "Google Scholar"),
    ("iacr", "IACR ePrint"),
    ("semantic", "Semantic Scholar"),
    ("crossref", "Crossref"),
    ("openalex", "OpenAlex"),
    ("pmc", "PubMed Central"),
    ("core", "CORE"),
    ("europepmc", "Europe PMC"),
    ("dblp", "dblp"),
    ("openaire", "OpenAIRE"),
    ("citeseerx", "CiteSeerX"),
    ("doaj", "DOAJ"),
    ("base", "BASE"),
    ("zenodo", "Zenodo"),
    ("hal", "HAL"),
    ("ssrn", "SSRN"),
    ("unpaywall", "Unpaywall"),
)
SUPPORTED_SOURCE_IDS = tuple(source_id for source_id, _label in SEARCH_SOURCE_OPTIONS)
DEFAULT_SOURCE_IDS = SUPPORTED_SOURCE_IDS
LEGACY_SOURCE_IDS = ("arxiv", "crossref")


class PaperSearchMcpError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpSearchBatch:
    query: str
    candidates: list[PaperCandidate]
    source_counts: dict[str, int]
    errors: dict[str, str]
    raw_total: int
    total: int = 0
    sources_used: tuple[str, ...] = ()
    duration_ms: int = 0


def normalize_source_ids(sources: Iterable[str]) -> tuple[str, ...]:
    requested = {str(source).strip().lower() for source in sources if str(source).strip()}
    if not requested:
        raise ValueError("至少选择一个论文来源")
    if "all" in requested:
        raise ValueError("必须传递明确的论文来源ID，不能使用all")

    unsupported = sorted(requested.difference(SUPPORTED_SOURCE_IDS))
    if unsupported:
        raise ValueError(f"不支持的论文来源: {', '.join(unsupported)}")
    return tuple(source_id for source_id in SUPPORTED_SOURCE_IDS if source_id in requested)


def normalize_queries(queries: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in queries:
        query = " ".join(str(value or "").split())
        key = query.casefold()
        if not query or key in seen:
            continue
        if len(query) > 240:
            raise ValueError("检索词不能超过240个字符")
        normalized.append(query)
        seen.add(key)
    if not normalized:
        raise ValueError("至少需要一个有效检索词")
    return tuple(normalized)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_url(value: Any) -> str:
    url = _clean_text(value)
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _normalize_doi(value: Any) -> str | None:
    doi = _clean_text(value)
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi or None


def _normalize_authors(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_clean_text(item) for item in value if _clean_text(item))
    return ", ".join(
        item.strip() for item in _clean_text(value).split(";") if item.strip()
    )


def _extra_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(value)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def paper_candidate_from_mcp(raw: dict[str, Any]) -> PaperCandidate:
    source = _clean_text(raw.get("source")).lower()
    if source not in SUPPORTED_SOURCE_IDS:
        raise ValueError(f"MCP论文返回了未知来源: {source or '<empty>'}")

    doi = _normalize_doi(raw.get("doi"))
    page_url = _normalize_url(raw.get("url"))
    if not page_url and doi:
        page_url = f"https://doi.org/{doi}"
    pdf_url = _normalize_url(raw.get("pdf_url")) or None

    extra = _extra_metadata(raw.get("extra"))
    publisher = _clean_text(raw.get("publisher") or extra.get("publisher"))
    if not publisher and source == "arxiv":
        publisher = "arXiv"

    return PaperCandidate(
        title=_clean_text(raw.get("title")) or "Untitled paper",
        authors=_normalize_authors(raw.get("authors")),
        summary=_clean_text(raw.get("abstract")),
        published=_clean_text(raw.get("published_date")),
        source=source,
        publisher=publisher or "Unknown publisher",
        doi=doi,
        page_url=page_url,
        pdf_url=pdf_url,
        status=(
            STATUS_AVAILABLE
            if can_attempt_download(source, pdf_url)
            else STATUS_LINK_ONLY
        ),
    )


def _tool_error_text(result: Any) -> str:
    messages = [
        _clean_text(getattr(item, "text", ""))
        for item in (getattr(result, "content", None) or [])
    ]
    return "; ".join(message for message in messages if message)[:1000]


def _unwrap_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "result" not in payload:
        return payload
    result_payload = payload["result"]
    if not isinstance(result_payload, dict):
        raise PaperSearchMcpError("MCP search_papers返回的result不是对象")
    return result_payload


def _tool_result_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise PaperSearchMcpError(_tool_error_text(result) or "MCP search_papers调用失败")

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return _unwrap_tool_payload(structured)

    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return _unwrap_tool_payload(parsed)
    raise PaperSearchMcpError("MCP search_papers没有返回结构化JSON")


def _payload_count(payload: dict[str, Any], field: str, default: int) -> int:
    value = payload.get(field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PaperSearchMcpError(f"MCP search_papers返回的{field}不是非负整数")
    return value


def _exception_group_message(exc: BaseException) -> str:
    messages: list[str] = []

    def collect(current: BaseException) -> None:
        if isinstance(current, BaseExceptionGroup):
            for nested in current.exceptions:
                collect(nested)
            return
        message = f"{type(current).__name__}: {current}"
        if message not in messages:
            messages.append(message)

    collect(exc)
    return "; ".join(messages)[:1000] or f"{type(exc).__name__}: {exc}"


def parse_search_papers_result(
    query: str,
    result: Any,
    *,
    selected_sources: Sequence[str],
) -> McpSearchBatch:
    payload = _tool_result_payload(result)
    selected = set(selected_sources)
    raw_papers = payload.get("papers")
    if not isinstance(raw_papers, list):
        raise PaperSearchMcpError("MCP search_papers返回的papers不是列表")

    errors = payload.get("errors")
    if errors is not None and not isinstance(errors, dict):
        raise PaperSearchMcpError("MCP search_papers返回的errors不是对象")
    normalized_errors = {
        _clean_text(source): _clean_text(error)[:500]
        for source, error in (errors.items() if isinstance(errors, dict) else [])
        if _clean_text(source) and _clean_text(error)
    }
    candidates: list[PaperCandidate] = []
    for index, raw in enumerate(raw_papers):
        if not isinstance(raw, dict):
            normalized_errors[f"paper:{index}"] = "论文记录不是对象"
            continue
        try:
            candidate = paper_candidate_from_mcp(raw)
        except (TypeError, ValueError) as exc:
            normalized_errors[f"paper:{index}"] = str(exc)[:500]
            continue
        if candidate.source not in selected:
            normalized_errors[f"paper:{index}"] = (
                f"返回来源{candidate.source}不在本次任务来源中"
            )
            continue
        candidates.append(candidate)

    source_results = payload.get("source_results")
    if source_results is not None and not isinstance(source_results, dict):
        raise PaperSearchMcpError("MCP search_papers返回的source_results不是对象")
    source_counts: dict[str, int] = {}
    for source, count in source_results.items() if isinstance(source_results, dict) else []:
        source_id = _clean_text(source).lower()
        if source_id not in selected:
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PaperSearchMcpError(
                f"MCP search_papers返回的source_results[{source_id}]不是非负整数"
            )
        source_counts[source_id] = count

    raw_sources_used = payload.get("sources_used")
    if raw_sources_used is not None and not isinstance(raw_sources_used, list):
        raise PaperSearchMcpError("MCP search_papers返回的sources_used不是列表")
    sources_used: list[str] = []
    for source in raw_sources_used or []:
        source_id = _clean_text(source).lower()
        if not source_id:
            continue
        if source_id not in selected:
            raise PaperSearchMcpError(
                f"MCP search_papers返回了未选择的来源: {source_id}"
            )
        if source_id not in sources_used:
            sources_used.append(source_id)
    if raw_sources_used is None:
        sources_used = [source for source in selected_sources if source in source_counts]

    total = _payload_count(payload, "total", len(raw_papers))
    raw_total = _payload_count(payload, "raw_total", total)
    return McpSearchBatch(
        query=query,
        candidates=candidates,
        source_counts=source_counts,
        errors=normalized_errors,
        raw_total=raw_total,
        total=total,
        sources_used=tuple(sources_used),
    )


async def search_queries_with_session(
    session: Any,
    queries: Iterable[str],
    *,
    sources: Iterable[str],
    max_results_per_source: int = DEFAULT_MAX_RESULTS_PER_SOURCE,
) -> list[McpSearchBatch]:
    normalized_queries = normalize_queries(queries)
    normalized_sources = normalize_source_ids(sources)
    if not 1 <= max_results_per_source <= 50:
        raise ValueError("max_results_per_source必须在1到50之间")

    source_argument = ",".join(normalized_sources)
    batches: list[McpSearchBatch] = []
    for query in normalized_queries:
        started = perf_counter()
        result = await session.call_tool(
            SEARCH_PAPERS_TOOL_NAME,
            arguments={
                "query": query,
                "sources": source_argument,
                "max_results_per_source": max_results_per_source,
            },
        )
        batches.append(
            replace(
                parse_search_papers_result(
                    query,
                    result,
                    selected_sources=normalized_sources,
                ),
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
            )
        )
    return batches


@asynccontextmanager
async def open_paper_search_mcp_session(
    *,
    command: str = DEFAULT_MCP_SERVER_COMMAND,
    args: Sequence[str] = DEFAULT_MCP_SERVER_ARGS,
    read_timeout_seconds: int = DEFAULT_MCP_READ_TIMEOUT_SECONDS,
) -> AsyncIterator[ClientSession]:
    parameters = StdioServerParameters(
        command=command,
        args=list(args),
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=read_timeout_seconds),
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            if SEARCH_PAPERS_TOOL_NAME not in {tool.name for tool in tools.tools}:
                raise PaperSearchMcpError("MCP Server没有暴露search_papers Tool")
            yield session


async def search_queries_via_mcp_async(
    queries: Iterable[str],
    *,
    sources: Iterable[str],
    max_results_per_source: int = DEFAULT_MAX_RESULTS_PER_SOURCE,
) -> list[McpSearchBatch]:
    async with open_paper_search_mcp_session() as session:
        return await search_queries_with_session(
            session,
            queries,
            sources=sources,
            max_results_per_source=max_results_per_source,
        )


def search_queries_via_mcp(
    queries: Iterable[str],
    *,
    sources: Iterable[str],
    max_results_per_source: int = DEFAULT_MAX_RESULTS_PER_SOURCE,
) -> list[McpSearchBatch]:
    try:
        return asyncio.run(
            search_queries_via_mcp_async(
                queries,
                sources=sources,
                max_results_per_source=max_results_per_source,
            )
        )
    except ExceptionGroup as exc:
        raise PaperSearchMcpError(_exception_group_message(exc)) from exc

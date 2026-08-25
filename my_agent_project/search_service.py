import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import Any

from .paper_search_mcp_service import (
    DEFAULT_MAX_RESULTS_PER_SOURCE,
    LEGACY_SOURCE_IDS,
    McpSearchBatch,
    normalize_queries,
    normalize_source_ids,
    search_queries_via_mcp,
)
from .schemas import PaperCandidate, STATUS_AVAILABLE, STATUS_FAILED, STATUS_LINK_ONLY


XML_NS = "http://www.w3.org/2005/Atom"


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").split())


def arxiv_pdf_url(page_url: str) -> str | None:
    if not page_url:
        return None
    if "/abs/" in page_url:
        return page_url.replace("/abs/", "/pdf/") + ".pdf"
    return None


def parse_arxiv_feed(xml_text: str) -> list[PaperCandidate]:
    root = ET.fromstring(xml_text)
    papers: list[PaperCandidate] = []
    for entry in root.findall(f"{{{XML_NS}}}entry"):
        title = normalize_text(entry.findtext(f"{{{XML_NS}}}title"))
        summary = normalize_text(entry.findtext(f"{{{XML_NS}}}summary"))
        published = normalize_text(entry.findtext(f"{{{XML_NS}}}published")).split("T")[0]
        page_url = normalize_text(entry.findtext(f"{{{XML_NS}}}id"))
        authors = ", ".join(
            normalize_text(author.findtext(f"{{{XML_NS}}}name"))
            for author in entry.findall(f"{{{XML_NS}}}author")
            if normalize_text(author.findtext(f"{{{XML_NS}}}name"))
        )
        papers.append(
            PaperCandidate(
                title=title or "Untitled arXiv paper",
                authors=authors,
                summary=summary,
                published=published,
                source="arxiv",
                publisher="arXiv",
                doi=None,
                page_url=page_url,
                pdf_url=arxiv_pdf_url(page_url),
                status=STATUS_AVAILABLE,
            )
        )
    return papers


def first_text(values: Any) -> str:
    if isinstance(values, list) and values:
        return normalize_text(str(values[0]))
    if isinstance(values, str):
        return normalize_text(values)
    return ""


def is_absolute_pdf_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.path.lower().endswith(".pdf")


def extract_crossref_pdf_url(item: dict[str, Any]) -> str | None:
    links = item.get("link") or []
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        url = normalize_text(link.get("URL"))
        content_type = normalize_text(link.get("content-type")).lower()
        if url and is_absolute_pdf_url(url):
            return url
        if url and content_type == "application/pdf" and is_absolute_pdf_url(url):
            return url
    return None


def parse_crossref_items(items: list[dict[str, Any]]) -> list[PaperCandidate]:
    papers: list[PaperCandidate] = []
    for item in items:
        title = first_text(item.get("title")) or "Untitled Crossref record"
        authors = ", ".join(
            " ".join(
                part
                for part in [
                    normalize_text(author.get("given")),
                    normalize_text(author.get("family")),
                ]
                if part
            )
            for author in item.get("author", [])
            if isinstance(author, dict)
        )
        published_parts = (
            item.get("published-print")
            or item.get("published-online")
            or item.get("created")
            or {}
        ).get("date-parts", [[]])
        published = "-".join(str(part) for part in published_parts[0]) if published_parts else ""
        page_url = normalize_text(item.get("URL"))
        pdf_url = extract_crossref_pdf_url(item)
        publisher = normalize_text(item.get("publisher")) or "Unknown publisher"
        papers.append(
            PaperCandidate(
                title=title,
                authors=authors,
                summary=first_text(item.get("abstract")),
                published=published,
                source="crossref",
                publisher=publisher,
                doi=normalize_text(item.get("DOI")) or None,
                page_url=page_url,
                pdf_url=pdf_url,
                status=STATUS_AVAILABLE if pdf_url else STATUS_LINK_ONLY,
            )
        )
    return papers


def _failed_search_candidate(source: str, error: str) -> PaperCandidate:
    return PaperCandidate(
        title=f"{source} failed",
        authors="",
        summary=error[:500],
        published="",
        source="system",
        publisher="",
        doi=None,
        page_url="",
        pdf_url=None,
        status=STATUS_FAILED,
    )


def _papers_from_mcp_batch(
    batch: McpSearchBatch,
    max_results: int,
) -> list[PaperCandidate]:
    failures = [
        _failed_search_candidate(source, error)
        for source, error in batch.errors.items()
    ]
    return (list(batch.candidates) + failures)[:max_results]


def search_many(
    queries: list[str],
    *,
    sources: list[str] | tuple[str, ...],
    max_results: int,
) -> list[McpSearchBatch]:
    normalized_queries = normalize_queries(queries)
    normalized_sources = normalize_source_ids(sources)
    if not 1 <= max_results <= 50:
        raise ValueError("max_results必须在1到50之间")

    try:
        batches = search_queries_via_mcp(
            normalized_queries,
            sources=normalized_sources,
            max_results_per_source=DEFAULT_MAX_RESULTS_PER_SOURCE,
        )
        if len(batches) != len(normalized_queries):
            raise RuntimeError("MCP search_papers返回了无效批次数量")
        return [
            replace(batch, candidates=list(batch.candidates[:max_results]))
            for batch in batches
        ]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        return [
            McpSearchBatch(
                query=query,
                candidates=[],
                source_counts={},
                errors={"paper_search_mcp": error},
                raw_total=0,
            )
            for query in normalized_queries
        ]


def search_all(query: str, max_results: int) -> list[PaperCandidate]:
    batch = search_many(
        [query],
        sources=LEGACY_SOURCE_IDS,
        max_results=max_results,
    )[0]
    return _papers_from_mcp_batch(batch, max_results)

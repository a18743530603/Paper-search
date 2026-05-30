import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import requests

from .schemas import PaperCandidate, STATUS_DOWNLOADING, STATUS_FAILED, STATUS_LINK_ONLY


ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
XML_NS = "http://www.w3.org/2005/Atom"
REQUEST_TIMEOUT = 20


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
                status=STATUS_DOWNLOADING,
            )
        )
    return papers


def search_arxiv(query: str, max_results: int) -> list[PaperCandidate]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    response = requests.get(ARXIV_API, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return parse_arxiv_feed(response.text)


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
                status=STATUS_DOWNLOADING if pdf_url else STATUS_LINK_ONLY,
            )
        )
    return papers


def search_crossref(query: str, max_results: int) -> list[PaperCandidate]:
    response = requests.get(
        CROSSREF_API,
        params={"query": query, "rows": max_results, "sort": "relevance"},
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "paper-hunter/0.1 (mailto:example@example.com)"},
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("message", {}).get("items", [])
    return parse_crossref_items(items if isinstance(items, list) else [])


def search_all(query: str, max_results: int) -> list[PaperCandidate]:
    per_source = max(1, max_results // 2)
    papers: list[PaperCandidate] = []
    for searcher in (search_arxiv, search_crossref):
        try:
            papers.extend(searcher(query, per_source))
        except Exception as exc:
            papers.append(
                PaperCandidate(
                    title=f"{searcher.__name__} failed",
                    authors="",
                    summary=str(exc),
                    published="",
                    source="system",
                    publisher="",
                    doi=None,
                    page_url="",
                    pdf_url=None,
                    status=STATUS_FAILED,
                )
            )
    return papers[:max_results]

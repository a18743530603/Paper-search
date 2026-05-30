from pathlib import Path

from my_agent_project.download_service import can_attempt_download, sanitize_filename
from my_agent_project.schemas import STATUS_DOWNLOADING, STATUS_LINK_ONLY
from my_agent_project.search_service import (
    parse_arxiv_feed,
    parse_crossref_items,
    search_all,
)


ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2401.00001v1</id>
    <title> Multimodal Agent: A Survey? </title>
    <summary> A useful summary. </summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Lee</name></author>
  </entry>
</feed>
"""


def test_parse_arxiv_feed_builds_pdf_url():
    papers = parse_arxiv_feed(ARXIV_XML)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.title == "Multimodal Agent: A Survey?"
    assert paper.authors == "Alice Smith, Bob Lee"
    assert paper.published == "2024-01-01"
    assert paper.page_url == "https://arxiv.org/abs/2401.00001v1"
    assert paper.pdf_url == "https://arxiv.org/pdf/2401.00001v1.pdf"
    assert paper.status == STATUS_DOWNLOADING


def test_parse_crossref_without_direct_pdf_is_link_only():
    papers = parse_crossref_items(
        [
            {
                "title": ["Crossref Paper"],
                "DOI": "10.1000/example",
                "URL": "https://doi.org/10.1000/example",
                "author": [{"given": "Carol", "family": "Wang"}],
                "created": {"date-parts": [[2025, 5, 1]]},
                "link": [{"URL": "https://publisher.example/article", "content-type": "text/html"}],
            }
        ]
    )

    assert len(papers) == 1
    assert papers[0].doi == "10.1000/example"
    assert papers[0].pdf_url is None
    assert papers[0].status == STATUS_LINK_ONLY


def test_parse_crossref_with_absolute_pdf_can_download():
    papers = parse_crossref_items(
        [
            {
                "title": ["Open PDF"],
                "URL": "https://doi.org/10.1000/pdf",
                "link": [{"URL": "https://example.org/file.pdf", "content-type": "application/pdf"}],
            }
        ]
    )

    assert papers[0].pdf_url == "https://example.org/file.pdf"
    assert papers[0].status == STATUS_DOWNLOADING


def test_sanitize_filename_removes_invalid_chars_and_limits_length():
    title = 'A:/Very*Long?Title"With<Bad>|Chars' + "x" * 200
    filename = sanitize_filename(title)

    assert filename.endswith(".pdf")
    assert len(Path(filename).stem) <= 100
    for char in '\\/*?:"<>|':
        assert char not in filename


def test_download_policy_prefers_arxiv_and_direct_pdf_only():
    assert can_attempt_download("arxiv", "https://arxiv.org/pdf/2401.00001.pdf")
    assert can_attempt_download("crossref", "https://example.org/article.pdf")
    assert not can_attempt_download("crossref", "https://example.org/article")
    assert not can_attempt_download("crossref", None)


def test_search_all_turns_source_failure_into_failed_record(monkeypatch):
    def fail_search(_query, _max_results):
        raise RuntimeError("network down")

    monkeypatch.setattr("my_agent_project.search_service.search_arxiv", fail_search)
    monkeypatch.setattr("my_agent_project.search_service.search_crossref", fail_search)

    papers = search_all("agent", 4)

    assert len(papers) == 2
    assert {paper.status for paper in papers} == {"failed"}

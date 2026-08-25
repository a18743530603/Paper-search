import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from my_agent_project import paper_search_mcp_service as service
from my_agent_project.schemas import STATUS_AVAILABLE, STATUS_LINK_ONLY


def _tool_result(payload: dict, *, is_error: bool = False):
    return SimpleNamespace(
        isError=is_error,
        structuredContent=payload,
        content=[],
    )


class FakeSession:
    def __init__(self, results: list):
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self.results.pop(0)


def test_source_ids_are_explicit_canonical_and_reject_all():
    assert service.normalize_source_ids(["crossref", "arxiv", "crossref"]) == (
        "arxiv",
        "crossref",
    )
    assert "all" not in service.DEFAULT_SOURCE_IDS
    assert len(service.DEFAULT_SOURCE_IDS) == 21

    with pytest.raises(ValueError, match="不能使用all"):
        service.normalize_source_ids(["all"])
    with pytest.raises(ValueError, match="至少选择"):
        service.normalize_source_ids([])
    with pytest.raises(ValueError, match="不支持"):
        service.normalize_source_ids(["arxiv", "unknown_source"])


def test_paper_mapping_preserves_pdf_metadata_without_network(monkeypatch):
    def fail_if_requested(*_args, **_kwargs):
        raise AssertionError("Paper mapping must not make an HTTP request")

    monkeypatch.setattr(
        "my_agent_project.download_service.requests.get",
        fail_if_requested,
    )
    candidate = service.paper_candidate_from_mcp(
        {
            "paper_id": "10.1000/test",
            "title": "  Search   Result  ",
            "authors": "Alice; Bob",
            "abstract": "An abstract.",
            "doi": "https://doi.org/10.1000/test",
            "published_date": "2026-08-24T00:00:00",
            "pdf_url": "https://example.org/paper.pdf",
            "url": "https://doi.org/10.1000/test",
            "source": "crossref",
            "extra": "{'publisher': 'Example Publisher'}",
        }
    )

    assert candidate.title == "Search Result"
    assert candidate.authors == "Alice, Bob"
    assert candidate.doi == "10.1000/test"
    assert candidate.publisher == "Example Publisher"
    assert candidate.pdf_url == "https://example.org/paper.pdf"
    assert candidate.status == STATUS_AVAILABLE

    link_only = service.paper_candidate_from_mcp(
        {
            "title": "Metadata only",
            "authors": [],
            "abstract": "",
            "doi": "",
            "published_date": "",
            "pdf_url": "https://example.org/article",
            "url": "https://example.org/article",
            "source": "openalex",
        }
    )
    assert link_only.pdf_url == "https://example.org/article"
    assert link_only.status == STATUS_LINK_ONLY


def test_partial_source_failure_keeps_healthy_candidates():
    result = _tool_result(
        {
            "papers": [
                {
                    "paper_id": "2401.00001",
                    "title": "Agent Memory",
                    "authors": "A. Researcher",
                    "abstract": "Memory architecture.",
                    "doi": "",
                    "published_date": "2026-01-01",
                    "pdf_url": "https://arxiv.org/pdf/2401.00001",
                    "url": "https://arxiv.org/abs/2401.00001",
                    "source": "arxiv",
                }
            ],
            "source_results": {"arxiv": 1, "crossref": 0},
            "errors": {"crossref": "upstream timeout"},
            "raw_total": 1,
        }
    )

    batch = service.parse_search_papers_result(
        "agent memory",
        result,
        selected_sources=("arxiv", "crossref"),
    )

    assert [candidate.title for candidate in batch.candidates] == ["Agent Memory"]
    assert batch.source_counts == {"arxiv": 1, "crossref": 0}
    assert batch.errors == {"crossref": "upstream timeout"}


def test_real_mcp_014_result_envelope_is_unwrapped_and_validated():
    payload = {
        "query": "agent memory",
        "sources_requested": "arxiv,crossref",
        "sources_used": ["arxiv", "crossref"],
        "source_results": {"arxiv": 1, "crossref": 0},
        "errors": {"crossref": "upstream timeout"},
        "papers": [
            {
                "paper_id": "2401.00001",
                "title": "Agent Memory",
                "authors": "A. Researcher",
                "abstract": "Memory architecture.",
                "doi": "",
                "published_date": "2026-01-01",
                "pdf_url": "https://arxiv.org/pdf/2401.00001",
                "url": "https://arxiv.org/abs/2401.00001",
                "source": "arxiv",
            }
        ],
        "total": 1,
        "raw_total": 2,
    }
    result = SimpleNamespace(
        isError=False,
        structuredContent={"result": payload},
        content=[SimpleNamespace(text=json.dumps(payload))],
    )

    batch = service.parse_search_papers_result(
        "agent memory",
        result,
        selected_sources=("arxiv", "crossref"),
    )

    assert [candidate.title for candidate in batch.candidates] == ["Agent Memory"]
    assert batch.sources_used == ("arxiv", "crossref")
    assert batch.source_counts == {"arxiv": 1, "crossref": 0}
    assert batch.errors == {"crossref": "upstream timeout"}
    assert batch.raw_total == 2
    assert batch.total == 1


def test_wrapped_payload_can_fall_back_to_text_content():
    payload = {
        "sources_used": ["crossref"],
        "source_results": {"crossref": 0},
        "errors": {},
        "papers": [],
        "total": 0,
        "raw_total": 0,
    }
    result = SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[SimpleNamespace(text=json.dumps({"result": payload}))],
    )

    batch = service.parse_search_papers_result(
        "bearing fault",
        result,
        selected_sources=("crossref",),
    )

    assert batch.sources_used == ("crossref",)
    assert batch.source_counts == {"crossref": 0}
    assert batch.raw_total == 0
    assert batch.total == 0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"result": []}, "result不是对象"),
        ({"result": {"papers": {}}}, "papers不是列表"),
        (
            {"result": {"papers": [], "source_results": []}},
            "source_results不是对象",
        ),
        (
            {"result": {"papers": [], "sources_used": "crossref"}},
            "sources_used不是列表",
        ),
        (
            {"result": {"papers": [], "total": "0"}},
            "total不是非负整数",
        ),
    ],
)
def test_malformed_result_envelope_fails_explicitly(payload, message):
    with pytest.raises(service.PaperSearchMcpError, match=message):
        service.parse_search_papers_result(
            "test",
            _tool_result(payload),
            selected_sources=("crossref",),
        )


def test_exception_group_preserves_leaf_cause(monkeypatch):
    async def fail_search(*_args, **_kwargs):
        raise ExceptionGroup(
            "transport wrapper",
            [service.PaperSearchMcpError("papers不是列表")],
        )

    monkeypatch.setattr(service, "search_queries_via_mcp_async", fail_search)

    with pytest.raises(service.PaperSearchMcpError, match="papers不是列表"):
        service.search_queries_via_mcp(
            ["agent memory"],
            sources=["crossref"],
        )


def test_multiple_queries_reuse_one_session_and_send_explicit_sources(monkeypatch):
    session = FakeSession(
        [
            _tool_result(
                {
                    "papers": [],
                    "source_results": {"arxiv": 0, "openalex": 0},
                    "errors": {},
                    "raw_total": 0,
                }
            )
            for _ in range(3)
        ]
    )
    opened = 0

    @asynccontextmanager
    async def fake_open_session():
        nonlocal opened
        opened += 1
        yield session

    monkeypatch.setattr(service, "open_paper_search_mcp_session", fake_open_session)
    batches = asyncio.run(
        service.search_queries_via_mcp_async(
            ["query one", "query two", "query three"],
            sources=["openalex", "arxiv"],
        )
    )

    assert opened == 1
    assert len(batches) == 3
    assert len(session.calls) == 3
    assert all(name == "search_papers" for name, _arguments in session.calls)
    assert all(
        arguments == {
            "query": expected_query,
            "sources": "arxiv,openalex",
            "max_results_per_source": 3,
        }
        for (_name, arguments), expected_query in zip(
            session.calls,
            ["query one", "query two", "query three"],
        )
    )


def test_unselected_or_malformed_papers_are_isolated():
    result = _tool_result(
        {
            "papers": [
                {"title": "Wrong source", "source": "pubmed"},
                "not-an-object",
                {
                    "title": "Valid paper",
                    "authors": [],
                    "abstract": "",
                    "doi": "",
                    "published_date": "",
                    "pdf_url": "",
                    "url": "https://example.org/valid",
                    "source": "arxiv",
                },
            ],
            "source_results": {"arxiv": 1},
            "errors": {},
            "raw_total": 3,
        }
    )

    batch = service.parse_search_papers_result(
        "test",
        result,
        selected_sources=("arxiv",),
    )

    assert [candidate.title for candidate in batch.candidates] == ["Valid paper"]
    assert batch.raw_total == 3
    assert set(batch.errors) == {"paper:0", "paper:1"}

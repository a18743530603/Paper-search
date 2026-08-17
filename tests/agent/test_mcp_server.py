import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from my_agent_project import mcp_server


def _paper_row() -> dict:
    return {
        "id": 7,
        "query": "agent memory",
        "title": "Reliable Agent Memory",
        "authors": "Test Author",
        "summary": "A controlled paper record.",
        "published": "2026-08-01",
        "source": "crossref",
        "publisher": "Test Publisher",
        "doi": "10.1000/test",
        "page_url": "https://doi.org/10.1000/test",
        "pdf_url": None,
        "local_path": "C:/private/papers/reliable-agent-memory.pdf",
        "status": "link_only",
        "error": None,
        "downloaded_at": None,
        "created_at": "2026-08-16 10:00:00",
        "updated_at": "2026-08-16 10:00:00",
    }


def _search_result(status: str = "succeeded") -> dict:
    return {
        "tool_name": "search_literature",
        "input": {"query": "agent memory", "max_results": 4},
        "status": status,
        "result_count": 1,
        "source_counts": {"crossref": 1},
        "error": "arXiv unavailable" if status == "partial" else "",
        "duration_ms": 12,
        "candidates": [
            {
                "title": "Reliable Agent Memory",
                "authors": "Test Author",
                "summary": "A controlled paper record.",
                "published": "2026-08-01",
                "source": "crossref",
                "publisher": "Test Publisher",
                "doi": "10.1000/test",
                "page_url": "https://doi.org/10.1000/test",
                "pdf_url": None,
                "status": "link_only",
            }
        ],
    }


def _evidence_result(status: str = "succeeded") -> dict:
    evidence = (
        [
            {
                "chunk_id": 31,
                "page_number": 4,
                "chunk_index": 2,
                "content": "Agent memory uses episodic and semantic stores.",
                "score": 0.91,
            }
        ]
        if status == "succeeded"
        else []
    )
    return {
        "tool_name": "retrieve_paper_evidence",
        "input": {
            "paper_id": 7,
            "question": "How is memory stored?",
            "top_k": 3,
        },
        "status": status,
        "result_count": len(evidence),
        "paper_title": "Reliable Agent Memory",
        "page_url": "https://doi.org/10.1000/test",
        "error": "" if evidence else "没有检索到相关全文证据",
        "duration_ms": 8,
        "evidence": evidence,
    }


def test_build_public_paper_metadata_hides_local_path(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_paper", lambda paper_id: _paper_row())

    result = mcp_server.build_public_paper_metadata(7)

    assert result["paper_id"] == 7
    assert result["title"] == "Reliable Agent Memory"
    assert result["page_url"] == "https://doi.org/10.1000/test"
    assert result["download_status"] == "link_only"
    assert "local_path" not in result


@pytest.mark.parametrize("paper_id", [0, -1])
def test_build_public_paper_metadata_rejects_invalid_id(paper_id):
    with pytest.raises(ValueError, match="paper_id必须为正整数"):
        mcp_server.build_public_paper_metadata(paper_id)


def test_build_public_paper_metadata_rejects_missing_paper(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_paper", lambda paper_id: None)

    with pytest.raises(LookupError, match="论文记录不存在"):
        mcp_server.build_public_paper_metadata(99)


def test_search_public_literature_delegates_to_registered_tool(monkeypatch):
    calls: list[tuple[str, dict]] = []
    expected = _search_result(status="partial")

    def invoke(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return expected

    monkeypatch.setattr(mcp_server, "invoke_research_tool", invoke)

    result = mcp_server.search_public_literature("agent memory", 4)

    assert result is expected
    assert calls == [
        (
            mcp_server.LITERATURE_SEARCH_TOOL_NAME,
            {"query": "agent memory", "max_results": 4},
        )
    ]


def test_search_public_literature_reuses_existing_validation(monkeypatch):
    search_calls = 0

    def fail_if_searched(*_args, **_kwargs):
        nonlocal search_calls
        search_calls += 1
        raise AssertionError("invalid query must not reach external search")

    monkeypatch.setattr("my_agent_project.research_tools.search_all", fail_if_searched)

    with pytest.raises(ValueError, match="检索词不能为空"):
        mcp_server.search_public_literature("   ", 4)

    assert search_calls == 0


def test_retrieve_public_evidence_delegates_without_preparation(monkeypatch):
    calls: list[tuple[str, dict]] = []
    expected = _evidence_result(status="failed")

    def invoke(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return expected

    monkeypatch.setattr(mcp_server, "invoke_research_tool", invoke)

    result = mcp_server.retrieve_public_paper_evidence(
        7,
        "How is memory stored?",
        3,
    )

    assert result is expected
    assert calls == [
        (
            mcp_server.RETRIEVE_EVIDENCE_TOOL_NAME,
            {
                "paper_id": 7,
                "question": "How is memory stored?",
                "top_k": 3,
            },
        )
    ]
    assert calls[0][0] != "prepare_paper_for_research"


def test_retrieve_public_evidence_reuses_existing_validation(monkeypatch):
    retrieval_calls = 0

    def fail_if_retrieved(*_args, **_kwargs):
        nonlocal retrieval_calls
        retrieval_calls += 1
        raise AssertionError("invalid question must not reach retrieval")

    monkeypatch.setattr(
        "my_agent_project.research_tools.retrieve_relevant_chunks",
        fail_if_retrieved,
    )

    with pytest.raises(ValueError, match="研究问题不能为空"):
        mcp_server.retrieve_public_paper_evidence(7, "   ", 3)

    assert retrieval_calls == 0


def test_mcp_client_lists_and_calls_metadata_tool(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_paper", lambda paper_id: _paper_row())

    async def run_client() -> None:
        server = mcp_server.create_mcp_server()
        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()
            tool_names = [tool.name for tool in tools.tools]
            assert tool_names == [
                mcp_server.GET_PAPER_METADATA_TOOL_NAME,
                mcp_server.SEARCH_LITERATURE_TOOL_NAME,
                mcp_server.RETRIEVE_PAPER_EVIDENCE_TOOL_NAME,
            ]

            result = await session.call_tool(
                mcp_server.GET_PAPER_METADATA_TOOL_NAME,
                arguments={"paper_id": 7},
            )

            assert result.isError is False
            assert result.structuredContent is not None
            assert result.structuredContent["paper_id"] == 7
            assert result.structuredContent["publisher"] == "Test Publisher"
            assert "local_path" not in result.structuredContent

    anyio.run(run_client)


def test_mcp_client_calls_literature_search_tool(monkeypatch):
    expected = _search_result(status="partial")
    calls: list[tuple[str, dict]] = []

    def invoke(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return expected

    monkeypatch.setattr(mcp_server, "invoke_research_tool", invoke)

    async def run_client() -> None:
        server = mcp_server.create_mcp_server()
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                mcp_server.SEARCH_LITERATURE_TOOL_NAME,
                arguments={"query": "agent memory", "max_results": 4},
            )

            assert result.isError is False
            assert result.structuredContent is not None
            assert result.structuredContent["status"] == "partial"
            assert result.structuredContent["source_counts"] == {"crossref": 1}
            assert result.structuredContent["candidates"][0]["title"] == (
                "Reliable Agent Memory"
            )

    anyio.run(run_client)
    assert calls == [
        (
            mcp_server.LITERATURE_SEARCH_TOOL_NAME,
            {"query": "agent memory", "max_results": 4},
        )
    ]


def test_mcp_client_calls_evidence_retrieval_tool(monkeypatch):
    expected = _evidence_result()
    calls: list[tuple[str, dict]] = []

    def invoke(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return expected

    monkeypatch.setattr(mcp_server, "invoke_research_tool", invoke)

    async def run_client() -> None:
        server = mcp_server.create_mcp_server()
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                mcp_server.RETRIEVE_PAPER_EVIDENCE_TOOL_NAME,
                arguments={
                    "paper_id": 7,
                    "question": "How is memory stored?",
                    "top_k": 3,
                },
            )

            assert result.isError is False
            assert result.structuredContent is not None
            assert result.structuredContent["status"] == "succeeded"
            assert result.structuredContent["evidence"][0]["page_number"] == 4
            assert result.structuredContent["evidence"][0]["score"] == 0.91

    anyio.run(run_client)
    assert calls[0][0] == mcp_server.RETRIEVE_EVIDENCE_TOOL_NAME


def test_mcp_client_receives_structured_tool_error(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_paper", lambda paper_id: None)

    async def run_client() -> None:
        server = mcp_server.create_mcp_server()
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                mcp_server.GET_PAPER_METADATA_TOOL_NAME,
                arguments={"paper_id": 99},
            )

            assert result.isError is True
            assert result.structuredContent is None
            assert "论文记录不存在" in result.content[0].text

    anyio.run(run_client)

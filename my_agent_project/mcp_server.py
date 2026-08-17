import warnings
from typing import Any


warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition:.*",
)

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import configure_utf8_stdio
from .db import get_paper
from .rag_service import paper_access_url
from .research_tools import (
    LITERATURE_SEARCH_TOOL_NAME,
    RETRIEVE_EVIDENCE_TOOL_NAME,
    invoke_research_tool,
)


MCP_SERVER_NAME = "paper-hunter"
GET_PAPER_METADATA_TOOL_NAME = "get_paper_metadata"
SEARCH_LITERATURE_TOOL_NAME = LITERATURE_SEARCH_TOOL_NAME
RETRIEVE_PAPER_EVIDENCE_TOOL_NAME = RETRIEVE_EVIDENCE_TOOL_NAME


def build_public_paper_metadata(paper_id: int) -> dict[str, Any]:
    """Return public paper metadata without exposing local filesystem paths."""
    if paper_id < 1:
        raise ValueError("paper_id必须为正整数")

    row = get_paper(paper_id)
    if row is None:
        raise LookupError("论文记录不存在")

    paper = dict(row)
    return {
        "paper_id": int(paper["id"]),
        "title": paper["title"],
        "authors": paper["authors"],
        "summary": paper["summary"],
        "published": paper["published"],
        "source": paper["source"],
        "publisher": paper["publisher"] or "未知",
        "doi": paper["doi"] or None,
        "page_url": paper_access_url(paper),
        "pdf_url": paper["pdf_url"] or None,
        "download_status": paper["status"],
        "downloaded_at": paper["downloaded_at"] or None,
        "created_at": paper["created_at"],
        "updated_at": paper["updated_at"],
    }


def search_public_literature(query: str, max_results: int = 10) -> dict[str, Any]:
    """Delegate metadata search to the existing validated research tool."""
    return invoke_research_tool(
        LITERATURE_SEARCH_TOOL_NAME,
        {"query": query, "max_results": max_results},
    )


def retrieve_public_paper_evidence(
    paper_id: int,
    question: str,
    top_k: int = 3,
) -> dict[str, Any]:
    """Delegate evidence retrieval without preparing or downloading the paper."""
    return invoke_research_tool(
        RETRIEVE_EVIDENCE_TOOL_NAME,
        {"paper_id": paper_id, "question": question, "top_k": top_k},
    )


def create_mcp_server() -> FastMCP:
    server = FastMCP(
        MCP_SERVER_NAME,
        instructions=(
            "Paper Hunter只读学术工具。允许检索公开论文元数据或查询已归档"
            "论文及其已索引证据，不得下载、解析、索引论文或暴露本地文件路径。"
        ),
        json_response=True,
        log_level="WARNING",
    )

    @server.tool(
        name=GET_PAPER_METADATA_TOOL_NAME,
        title="获取论文元数据",
        description="按Paper Hunter论文ID读取公开元数据和访问状态。",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def get_paper_metadata(paper_id: int) -> dict[str, Any]:
        return build_public_paper_metadata(paper_id)

    @server.tool(
        name=SEARCH_LITERATURE_TOOL_NAME,
        title="检索学术论文",
        description="从arXiv和Crossref检索公开论文元数据，不下载或写入数据库。",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    def search_literature(query: str, max_results: int = 10) -> dict[str, Any]:
        return search_public_literature(query, max_results)

    @server.tool(
        name=RETRIEVE_PAPER_EVIDENCE_TOOL_NAME,
        title="检索论文证据",
        description=(
            "从已完成索引的论文中检索带物理页码的证据；"
            "不会自动下载、解析或索引论文。"
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    def retrieve_paper_evidence(
        paper_id: int,
        question: str,
        top_k: int = 3,
    ) -> dict[str, Any]:
        return retrieve_public_paper_evidence(paper_id, question, top_k)

    return server


mcp = create_mcp_server()


def main() -> None:
    configure_utf8_stdio()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

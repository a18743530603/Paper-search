import json
import sys
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition:.*",
)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from my_agent_project.config import configure_utf8_stdio  # noqa: E402
from my_agent_project.mcp_server import (  # noqa: E402
    GET_PAPER_METADATA_TOOL_NAME,
    MCP_SERVER_NAME,
    RETRIEVE_PAPER_EVIDENCE_TOOL_NAME,
    SEARCH_LITERATURE_TOOL_NAME,
)


EXPECTED_TOOL_NAMES = [
    GET_PAPER_METADATA_TOOL_NAME,
    SEARCH_LITERATURE_TOOL_NAME,
    RETRIEVE_PAPER_EVIDENCE_TOOL_NAME,
]


async def inspect_stdio_server() -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "my_agent_project.mcp_server"],
        cwd=BASE_DIR,
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=10),
        ) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()

    tool_names = [tool.name for tool in tools.tools]
    if tool_names != EXPECTED_TOOL_NAMES:
        raise RuntimeError(
            "MCP Tool清单不匹配："
            f"expected={EXPECTED_TOOL_NAMES}, actual={tool_names}"
        )
    if initialized.serverInfo.name != MCP_SERVER_NAME:
        raise RuntimeError(
            "MCP Server名称不匹配："
            f"expected={MCP_SERVER_NAME}, actual={initialized.serverInfo.name}"
        )

    return {
        "status": "passed",
        "server_name": initialized.serverInfo.name,
        "server_version": initialized.serverInfo.version,
        "protocol_version": initialized.protocolVersion,
        "transport": "stdio",
        "tool_count": len(tool_names),
        "tools": tool_names,
    }


def main() -> int:
    configure_utf8_stdio()
    try:
        report = anyio.run(inspect_stdio_server)
    except Exception as exc:
        print(f"MCP stdio smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

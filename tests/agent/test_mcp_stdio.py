import anyio

from scripts.smoke_mcp_client import EXPECTED_TOOL_NAMES, inspect_stdio_server


def test_stdio_client_discovers_expected_tools_from_child_process():
    report = anyio.run(inspect_stdio_server)

    assert report["status"] == "passed"
    assert report["server_name"] == "paper-hunter"
    assert report["transport"] == "stdio"
    assert report["tool_count"] == 3
    assert report["tools"] == EXPECTED_TOOL_NAMES
    assert report["protocol_version"]

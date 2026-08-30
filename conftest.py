import os
import tempfile


_PYTEST_RUNTIME = tempfile.TemporaryDirectory(prefix="paper-hunter-pytest-")

# Test collection imports application modules, and main.py initializes the selected
# Business Database at import time. Force an isolated, explicit SQLite backend before
# any test module can import project configuration. Real MySQL tests later opt into
# randomly named temporary databases and never use the formal paper_hunter database.
os.environ["BUSINESS_DB_BACKEND"] = "sqlite"
os.environ["DOWNLOAD_DIR"] = _PYTEST_RUNTIME.name
os.environ["RESEARCH_QUERY_ROUTING_ENABLED"] = "false"
os.environ["RESEARCH_PRF_ENABLED"] = "false"


def pytest_sessionfinish(session, exitstatus) -> None:
    from my_agent_project import research_checkpoint

    connection = research_checkpoint._checkpoint_connection
    if connection is not None:
        connection.close()
        research_checkpoint._checkpoint_connection = None
        research_checkpoint._checkpointer = None
        research_checkpoint._research_graph = None
    _PYTEST_RUNTIME.cleanup()

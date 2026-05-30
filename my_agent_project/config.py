import io
import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR / "downloads"))
PAPERS_DIR = DOWNLOAD_DIR / "papers"
EXPORTS_DIR = DOWNLOAD_DIR / "exports"
DATABASE_PATH = DOWNLOAD_DIR / "metadata.db"
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))
ENABLE_AGENT = os.getenv("ENABLE_AGENT", "false").lower() in {"1", "true", "yes", "on"}


def configure_utf8_stdio() -> None:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
        elif hasattr(stream, "buffer"):
            setattr(sys, stream_name, io.TextIOWrapper(stream.buffer, encoding="utf-8"))


def ensure_runtime_dirs() -> None:
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


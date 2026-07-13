import io
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR / "downloads"))
PAPERS_DIR = DOWNLOAD_DIR / "papers"
EXPORTS_DIR = DOWNLOAD_DIR / "exports"
ORIGIN_EXPORTS_DIR = EXPORTS_DIR / "origin"
DATABASE_PATH = DOWNLOAD_DIR / "metadata.db"
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))
ENABLE_AGENT = os.getenv("ENABLE_AGENT", "false").lower() in {"1", "true", "yes", "on"}
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip()
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "120"))
SEED_API_KEY = os.getenv("SEED_API_KEY", "").strip()
SEED_BASE_URL = os.getenv(
    "SEED_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/v3",
).rstrip("/")
SEED_EMBEDDING_MODEL = os.getenv("SEED_EMBEDDING_MODEL", "").strip()
SEED_TIMEOUT = float(os.getenv("SEED_TIMEOUT", "120"))


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
    ORIGIN_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

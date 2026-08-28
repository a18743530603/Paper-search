import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR / "downloads"))
PAPERS_DIR = DOWNLOAD_DIR / "papers"
EXPORTS_DIR = DOWNLOAD_DIR / "exports"
ORIGIN_EXPORTS_DIR = EXPORTS_DIR / "origin"
DATABASE_PATH = DOWNLOAD_DIR / "metadata.db"
LANGGRAPH_CHECKPOINT_PATH = DOWNLOAD_DIR / "langgraph_checkpoints.db"


@dataclass(frozen=True)
class BusinessDatabaseConfig:
    backend: str
    host: str
    port: int
    name: str
    user: str
    password: str
    connect_timeout: int
    read_timeout: int
    write_timeout: int


def _positive_int_setting(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw_value = environ.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def load_business_database_config(
    environ: Mapping[str, str] | None = None,
) -> BusinessDatabaseConfig:
    values = os.environ if environ is None else environ
    backend = values.get("BUSINESS_DB_BACKEND", "mysql").strip().lower()
    if backend not in {"sqlite", "mysql"}:
        raise ValueError("BUSINESS_DB_BACKEND must be 'sqlite' or 'mysql'")
    return BusinessDatabaseConfig(
        backend=backend,
        host=values.get("BUSINESS_DB_HOST", "127.0.0.1").strip(),
        port=_positive_int_setting(values, "BUSINESS_DB_PORT", 3306),
        name=values.get("BUSINESS_DB_NAME", "paper_hunter").strip(),
        user=values.get("BUSINESS_DB_USER", "").strip(),
        password=values.get("BUSINESS_DB_PASSWORD", ""),
        connect_timeout=_positive_int_setting(
            values,
            "BUSINESS_DB_CONNECT_TIMEOUT",
            10,
        ),
        read_timeout=_positive_int_setting(values, "BUSINESS_DB_READ_TIMEOUT", 30),
        write_timeout=_positive_int_setting(values, "BUSINESS_DB_WRITE_TIMEOUT", 30),
    )


BUSINESS_DATABASE = load_business_database_config()
BUSINESS_DB_BACKEND = BUSINESS_DATABASE.backend
BUSINESS_DB_HOST = BUSINESS_DATABASE.host
BUSINESS_DB_PORT = BUSINESS_DATABASE.port
BUSINESS_DB_NAME = BUSINESS_DATABASE.name
BUSINESS_DB_USER = BUSINESS_DATABASE.user
BUSINESS_DB_PASSWORD = BUSINESS_DATABASE.password
BUSINESS_DB_CONNECT_TIMEOUT = BUSINESS_DATABASE.connect_timeout
BUSINESS_DB_READ_TIMEOUT = BUSINESS_DATABASE.read_timeout
BUSINESS_DB_WRITE_TIMEOUT = BUSINESS_DATABASE.write_timeout
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
SEED_EMBEDDING_API_MODE = os.getenv("SEED_EMBEDDING_API_MODE", "auto").strip().lower()
SEED_TIMEOUT = float(os.getenv("SEED_TIMEOUT", "120"))
MINERU_API_TOKEN = (
    os.getenv("MINERU_API_TOKEN", "").strip()
    or os.getenv("MINERU_TOKEN", "").strip()
)
MINERU_BASE_URL = os.getenv(
    "MINERU_BASE_URL",
    "https://mineru.net/api/v4",
).rstrip("/")
MINERU_MODEL_VERSION = os.getenv("MINERU_MODEL_VERSION", "vlm").strip().lower()
MINERU_REQUEST_TIMEOUT = float(os.getenv("MINERU_REQUEST_TIMEOUT", "120"))
MINERU_PARSE_TIMEOUT = float(os.getenv("MINERU_PARSE_TIMEOUT", "1200"))
MINERU_POLL_INTERVAL = float(os.getenv("MINERU_POLL_INTERVAL", "5"))
MINERU_RESULTS_DIR = DOWNLOAD_DIR / "mineru"


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
    MINERU_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

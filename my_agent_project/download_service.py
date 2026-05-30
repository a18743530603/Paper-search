import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import PAPERS_DIR, ensure_runtime_dirs
from .db import get_paper, update_download_status
from .schemas import STATUS_DOWNLOADED, STATUS_FAILED, STATUS_LINK_ONLY


INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|]')
DOWNLOAD_TIMEOUT = 40


def sanitize_filename(title: str, suffix: str = ".pdf") -> str:
    stem = INVALID_FILENAME_CHARS.sub("_", title or "untitled").strip(" ._")
    stem = re.sub(r"\s+", " ", stem)[:100].strip(" ._")
    if not stem:
        stem = "untitled"
    return f"{stem}{suffix}"


def unique_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Too many duplicate filenames")


def can_attempt_download(source: str, pdf_url: str | None) -> bool:
    if not pdf_url:
        return False
    parsed = urlparse(pdf_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if source == "arxiv":
        return True
    return parsed.path.lower().endswith(".pdf")


def download_paper(paper_id: int) -> None:
    paper = get_paper(paper_id)
    if paper is None:
        return

    pdf_url = paper["pdf_url"]
    source = paper["source"]
    if not can_attempt_download(source, pdf_url):
        update_download_status(paper_id, STATUS_LINK_ONLY, error=None)
        return

    try:
        ensure_runtime_dirs()
        response = requests.get(pdf_url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if source != "arxiv" and "pdf" not in content_type:
            update_download_status(paper_id, STATUS_LINK_ONLY, error="No direct PDF response")
            return

        filename = sanitize_filename(paper["title"])
        path = unique_path(PAPERS_DIR, filename)
        with path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)

        if path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            raise RuntimeError("Downloaded file is empty")

        update_download_status(paper_id, STATUS_DOWNLOADED, local_path=str(path), error=None)
    except Exception as exc:
        update_download_status(paper_id, STATUS_FAILED, error=str(exc)[:500])


from typing import Any, Mapping

from .db import (
    claim_download,
    get_document,
    get_or_create_document,
    get_paper,
    update_download_status,
    update_index_status,
    update_parse_status,
)
from .download_service import can_attempt_download, download_paper
from .pdf_service import parse_paper_pdf
from .rag_service import index_paper_chunks
from .schemas import (
    INDEX_FAILED,
    INDEX_INDEXED,
    INDEX_INDEXING,
    PARSE_FAILED,
    PARSE_PARSED,
    PARSE_PARSING,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_AVAILABLE,
)


PREPARATION_AVAILABLE = "available"
PREPARATION_DOWNLOADING = "downloading"
PREPARATION_DOWNLOAD_FAILED = "download_failed"
PREPARATION_AWAITING_PARSE = "awaiting_parse"
PREPARATION_PARSING = "parsing"
PREPARATION_PARSE_FAILED = "parse_failed"
PREPARATION_AWAITING_INDEX = "awaiting_index"
PREPARATION_INDEXING = "indexing"
PREPARATION_INDEX_FAILED = "index_failed"
PREPARATION_READY = "ready"
PREPARATION_LINK_ONLY = "link_only"

PREPARATION_RUNNING = {
    PREPARATION_DOWNLOADING,
    PREPARATION_PARSING,
    PREPARATION_INDEXING,
}


def paper_preparation_status(
    paper: Mapping[str, Any],
    document: Mapping[str, Any] | None,
) -> str:
    if paper.get("local_path"):
        if document is None:
            return PREPARATION_AWAITING_PARSE
        parse_status = document.get("parse_status")
        if parse_status == PARSE_PARSING:
            return PREPARATION_PARSING
        if parse_status == PARSE_FAILED:
            return PREPARATION_PARSE_FAILED
        if parse_status != PARSE_PARSED:
            return PREPARATION_AWAITING_PARSE

        index_status = document.get("index_status")
        if index_status == INDEX_INDEXING:
            return PREPARATION_INDEXING
        if index_status == INDEX_FAILED:
            return PREPARATION_INDEX_FAILED
        if index_status == INDEX_INDEXED:
            return PREPARATION_READY
        return PREPARATION_AWAITING_INDEX

    if paper.get("status") == STATUS_DOWNLOADING:
        return PREPARATION_DOWNLOADING
    if paper.get("status") == STATUS_FAILED:
        return PREPARATION_DOWNLOAD_FAILED
    if can_attempt_download(str(paper.get("source") or ""), paper.get("pdf_url")):
        return PREPARATION_AVAILABLE
    return PREPARATION_LINK_ONLY


def get_paper_preparation_status(paper_id: int) -> str | None:
    paper = get_paper(paper_id)
    if paper is None:
        return None
    document = get_document(paper_id)
    return paper_preparation_status(dict(paper), dict(document) if document else None)


def mark_preparation_started(paper_id: int) -> str:
    paper = get_paper(paper_id)
    if paper is None:
        raise LookupError("论文记录不存在")
    document = get_document(paper_id)
    status = paper_preparation_status(
        dict(paper),
        dict(document) if document else None,
    )
    if status in PREPARATION_RUNNING:
        raise RuntimeError("论文正在准备中")
    if status == PREPARATION_LINK_ONLY:
        raise RuntimeError("论文没有可下载的 PDF 直链")
    if status == PREPARATION_READY:
        return status

    if not paper["local_path"]:
        if not claim_download(paper_id, (STATUS_AVAILABLE, STATUS_FAILED)):
            raise RuntimeError("论文下载任务已由其他请求启动或状态已改变")
        return PREPARATION_DOWNLOADING

    get_or_create_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        update_parse_status(paper_id, PARSE_PARSING, error=None)
        return PREPARATION_PARSING

    update_index_status(paper_id, INDEX_INDEXING, error=None)
    return PREPARATION_INDEXING


def prepare_paper_for_qa(
    paper_id: int,
    *,
    download_claimed: bool = False,
) -> str | None:
    paper = get_paper(paper_id)
    if paper is None:
        return None

    stage = "download"
    try:
        if not paper["local_path"]:
            if not can_attempt_download(paper["source"], paper["pdf_url"]):
                return PREPARATION_LINK_ONLY
            if not download_claimed and not claim_download(
                paper_id,
                (STATUS_AVAILABLE, STATUS_FAILED),
            ):
                return get_paper_preparation_status(paper_id)
            download_paper(paper_id)
            paper = get_paper(paper_id)
            if (
                paper is None
                or paper["status"] != STATUS_DOWNLOADED
                or not paper["local_path"]
            ):
                return get_paper_preparation_status(paper_id)

        stage = "parse"
        document = get_or_create_document(paper_id)
        if document["parse_status"] != PARSE_PARSED:
            parse_paper_pdf(paper_id)
            document = get_document(paper_id)
            if document is None or document["parse_status"] != PARSE_PARSED:
                return get_paper_preparation_status(paper_id)

        stage = "index"
        if document["index_status"] != INDEX_INDEXED:
            index_paper_chunks(paper_id)
        return get_paper_preparation_status(paper_id)
    except Exception as exc:
        message = str(exc)[:500]
        if stage == "download":
            update_download_status(paper_id, STATUS_FAILED, error=message)
        elif stage == "parse":
            update_parse_status(paper_id, PARSE_FAILED, error=message)
        else:
            update_index_status(paper_id, INDEX_FAILED, error=message)
        return get_paper_preparation_status(paper_id)

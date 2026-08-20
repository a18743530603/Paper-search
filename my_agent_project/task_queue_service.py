import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from . import db
from .download_service import download_paper
from .pdf_service import parse_paper_pdf
from .paper_workflow import (
    PREPARATION_DOWNLOAD_FAILED,
    PREPARATION_DOWNLOADING,
    PREPARATION_INDEX_FAILED,
    PREPARATION_INDEXING,
    PREPARATION_PARSE_FAILED,
    PREPARATION_PARSING,
    PREPARATION_READY,
    get_paper_preparation_status,
    prepare_paper_for_qa,
)
from .schemas import INDEX_FAILED, INDEX_INDEXING, PARSE_FAILED, PARSE_PARSING
from .schemas import (
    INDEX_INDEXED,
    PARSE_PARSED,
    RAG_ANSWERED,
    RAG_ANSWERING,
    RAG_FAILED,
    RESEARCH_TASK_AWAITING_SELECTION,
    RESEARCH_TASK_RUNNING,
    RESEARCH_REPORT_COMPLETED,
    RESEARCH_REPORT_RUNNING,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
)
from .rag_service import answer_rag_query, index_paper_chunks
from .research_workflow import execute_research_task
from .research_workflow import execute_research_report


JOB_DOWNLOAD_PAPER = "download_paper"
JOB_PREPARE_PAPER = "prepare_paper"
JOB_PARSE_PAPER = "parse_paper"
JOB_INDEX_PAPER = "index_paper"
JOB_ANSWER_RAG_QUERY = "answer_rag_query"
JOB_RESEARCH_SEARCH = "research_search"
JOB_RESEARCH_REPORT = "research_report"
JOB_STATUS_RETRY_WAIT = "retry_wait"
JOB_STATUS_FAILED = "failed"
DEFAULT_JOB_LEASE_SECONDS = 90

JobHandler = Callable[[dict[str, Any]], None]


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def _download_paper_handler(payload: dict[str, Any]) -> None:
    paper_id = int(payload.get("paper_id") or 0)
    if paper_id < 1 or db.get_paper(paper_id) is None:
        raise RuntimeError("Download job references a missing paper")
    download_paper(paper_id, raise_on_error=True)


def _preparation_error(paper_id: int, status: str) -> str:
    paper = db.get_paper(paper_id)
    document = db.get_document(paper_id)
    if status == PREPARATION_DOWNLOAD_FAILED and paper is not None:
        return str(paper["error"] or "Paper download failed")
    if status == PREPARATION_PARSE_FAILED and document is not None:
        return str(document["error"] or "Paper parsing failed")
    if status == PREPARATION_INDEX_FAILED and document is not None:
        return str(document["index_error"] or "Paper indexing failed")
    return f"Paper preparation stopped at status: {status}"


def _prepare_paper_handler(payload: dict[str, Any]) -> None:
    paper_id = int(payload.get("paper_id") or 0)
    if paper_id < 1 or db.get_paper(paper_id) is None:
        raise RuntimeError("Preparation job references a missing paper")
    status = prepare_paper_for_qa(
        paper_id,
        download_claimed=bool(payload.get("download_claimed")),
    )
    if status != PREPARATION_READY:
        raise RuntimeError(_preparation_error(paper_id, str(status or "unknown")))


def _parse_paper_handler(payload: dict[str, Any]) -> None:
    paper_id = int(payload.get("paper_id") or 0)
    if paper_id < 1 or db.get_paper(paper_id) is None:
        raise RuntimeError("Parse job references a missing paper")
    parse_paper_pdf(paper_id)
    document = db.get_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        error = document["error"] if document is not None else None
        raise RuntimeError(str(error or "Paper parsing failed"))


def _index_paper_handler(payload: dict[str, Any]) -> None:
    paper_id = int(payload.get("paper_id") or 0)
    if paper_id < 1 or db.get_paper(paper_id) is None:
        raise RuntimeError("Index job references a missing paper")
    index_paper_chunks(paper_id)
    document = db.get_document(paper_id)
    if document is None or document["index_status"] != INDEX_INDEXED:
        error = document["index_error"] if document is not None else None
        raise RuntimeError(str(error or "Paper indexing failed"))


def _answer_rag_query_handler(payload: dict[str, Any]) -> None:
    query_id = int(payload.get("query_id") or 0)
    query = db.get_rag_query(query_id)
    if query_id < 1 or query is None:
        raise RuntimeError("RAG answer job references a missing query")
    answer_rag_query(query_id, raise_on_error=True)
    query = db.get_rag_query(query_id)
    if query is None or query["status"] != RAG_ANSWERED:
        error = query["error"] if query is not None else None
        raise RuntimeError(str(error or "RAG answer generation failed"))


def _research_search_handler(payload: dict[str, Any]) -> None:
    task_id = int(payload.get("task_id") or 0)
    task = db.get_research_task(task_id)
    if task_id < 1 or task is None:
        raise RuntimeError("Research search job references a missing task")
    if task["status"] == RESEARCH_TASK_AWAITING_SELECTION:
        return
    if task["status"] != RESEARCH_TASK_RUNNING:
        raise RuntimeError(
            f"Research task cannot run from status: {task['status']}"
        )
    execute_research_task(task_id, raise_on_error=True)
    task = db.get_research_task(task_id)
    if task is None or task["status"] != RESEARCH_TASK_AWAITING_SELECTION:
        error = task["error"] if task is not None else None
        raise RuntimeError(str(error or "Research search did not produce candidates"))


def _research_report_handler(payload: dict[str, Any]) -> None:
    task_id = int(payload.get("task_id") or 0)
    task = db.get_research_task(task_id)
    report = db.get_research_report(task_id)
    if task_id < 1 or task is None:
        raise RuntimeError("Research report job references a missing task")
    if report is not None and report["status"] == RESEARCH_REPORT_COMPLETED:
        return
    if report is None or report["status"] not in RESEARCH_REPORT_RUNNING:
        status = report["status"] if report is not None else "missing"
        raise RuntimeError(f"Research report cannot run from status: {status}")
    execute_research_report(task_id)
    report = db.get_research_report(task_id)
    if report is None or report["status"] != RESEARCH_REPORT_COMPLETED:
        error = report["error"] if report is not None else None
        raise RuntimeError(str(error or "Research report did not complete"))


JOB_HANDLERS: dict[str, JobHandler] = {
    JOB_DOWNLOAD_PAPER: _download_paper_handler,
    JOB_PREPARE_PAPER: _prepare_paper_handler,
    JOB_PARSE_PAPER: _parse_paper_handler,
    JOB_INDEX_PAPER: _index_paper_handler,
    JOB_ANSWER_RAG_QUERY: _answer_rag_query_handler,
    JOB_RESEARCH_SEARCH: _research_search_handler,
    JOB_RESEARCH_REPORT: _research_report_handler,
}


def _mark_preparation_retry_pending(paper_id: int, error: str) -> None:
    status = get_paper_preparation_status(paper_id)
    if status == PREPARATION_DOWNLOAD_FAILED:
        db.update_download_status(
            paper_id,
            STATUS_DOWNLOADING,
            error=f"Preparation download failed; waiting for retry: {error}"[:500],
        )
    elif status == PREPARATION_PARSE_FAILED:
        db.update_parse_status(
            paper_id,
            PARSE_PARSING,
            error=f"Paper parsing failed; waiting for retry: {error}"[:500],
        )
    elif status == PREPARATION_INDEX_FAILED:
        db.update_index_status(
            paper_id,
            INDEX_INDEXING,
            error=f"Paper indexing failed; waiting for retry: {error}"[:500],
        )


def _mark_interrupted_preparation_failed(paper_id: int, error: str) -> None:
    status = get_paper_preparation_status(paper_id)
    if status == PREPARATION_DOWNLOADING:
        db.update_download_status(paper_id, STATUS_FAILED, error=error[:500])
    elif status == PREPARATION_PARSING:
        db.update_parse_status(paper_id, PARSE_FAILED, error=error[:500])
    elif status == PREPARATION_INDEXING:
        db.update_index_status(paper_id, INDEX_FAILED, error=error[:500])


def _mark_stage_job_retry_pending(job_type: str, paper_id: int, error: str) -> None:
    if job_type == JOB_PARSE_PAPER:
        db.update_parse_status(
            paper_id,
            PARSE_PARSING,
            error=f"Paper parsing failed; waiting for retry: {error}"[:500],
        )
    elif job_type == JOB_INDEX_PAPER:
        db.update_index_status(
            paper_id,
            INDEX_INDEXING,
            error=f"Paper indexing failed; waiting for retry: {error}"[:500],
        )


def _mark_stage_job_failed(job_type: str, paper_id: int, error: str) -> None:
    if job_type == JOB_PARSE_PAPER:
        document = db.get_document(paper_id)
        if document is not None and document["parse_status"] == PARSE_PARSING:
            db.update_parse_status(paper_id, PARSE_FAILED, error=error[:500])
    elif job_type == JOB_INDEX_PAPER:
        db.update_index_status(paper_id, INDEX_FAILED, error=error[:500])


def _mark_rag_job_retry_pending(query_id: int, error: str) -> None:
    query = db.get_rag_query(query_id)
    if query is not None and query["status"] != RAG_ANSWERED:
        db.update_rag_query(
            query_id,
            RAG_ANSWERING,
            error=f"Answer generation failed; waiting for retry: {error}"[:500],
        )


def _mark_rag_job_failed(query_id: int, error: str) -> None:
    query = db.get_rag_query(query_id)
    if query is not None and query["status"] != RAG_ANSWERED:
        db.update_rag_query(query_id, RAG_FAILED, error=error[:500])


def _mark_research_search_retry_pending(task_id: int, error: str) -> None:
    task = db.get_research_task(task_id)
    if task is not None and task["status"] != RESEARCH_TASK_AWAITING_SELECTION:
        db.mark_research_search_retry_pending(
            task_id,
            f"Research search failed; waiting for retry: {error}"[:1000],
        )


def _mark_research_search_failed(task_id: int, error: str) -> None:
    task = db.get_research_task(task_id)
    if task is not None and task["status"] != RESEARCH_TASK_AWAITING_SELECTION:
        db.fail_research_task(task_id, error[:1000])


def _mark_research_report_retry_pending(task_id: int, error: str) -> None:
    report = db.get_research_report(task_id)
    if report is not None and report["status"] != RESEARCH_REPORT_COMPLETED:
        db.mark_research_report_retry_pending(
            task_id,
            f"Research report failed; waiting for retry: {error}"[:1000],
        )


def _mark_research_report_failed(task_id: int, error: str) -> None:
    report = db.get_research_report(task_id)
    if report is not None and report["status"] != RESEARCH_REPORT_COMPLETED:
        db.update_research_report(task_id, "failed", error=error[:1000])


def recover_expired_jobs() -> list[dict]:
    recovered = db.recover_expired_background_jobs()
    for job in recovered:
        if job["job_type"] == JOB_RESEARCH_REPORT and job.get("research_task_id"):
            task_id = int(job["research_task_id"])
            if job["status"] == JOB_STATUS_FAILED:
                _mark_research_report_failed(task_id, job["last_error"])
            else:
                _mark_research_report_retry_pending(task_id, job["last_error"])
            continue
        if job["job_type"] == JOB_RESEARCH_SEARCH and job.get("research_task_id"):
            task_id = int(job["research_task_id"])
            if job["status"] == JOB_STATUS_FAILED:
                _mark_research_search_failed(task_id, job["last_error"])
            else:
                _mark_research_search_retry_pending(task_id, job["last_error"])
            continue
        if job["job_type"] == JOB_ANSWER_RAG_QUERY and job.get("rag_query_id"):
            query_id = int(job["rag_query_id"])
            if job["status"] == JOB_STATUS_FAILED:
                _mark_rag_job_failed(query_id, job["last_error"])
            else:
                _mark_rag_job_retry_pending(query_id, job["last_error"])
            continue
        if job["job_type"] != JOB_DOWNLOAD_PAPER or not job.get("paper_id"):
            if (
                job["job_type"] == JOB_PREPARE_PAPER
                and job.get("paper_id")
                and job["status"] == JOB_STATUS_FAILED
            ):
                _mark_interrupted_preparation_failed(
                    int(job["paper_id"]),
                    job["last_error"],
                )
            elif (
                job["job_type"] in {JOB_PARSE_PAPER, JOB_INDEX_PAPER}
                and job.get("paper_id")
                and job["status"] == JOB_STATUS_FAILED
            ):
                _mark_stage_job_failed(
                    job["job_type"],
                    int(job["paper_id"]),
                    job["last_error"],
                )
            continue
        paper_id = int(job["paper_id"])
        if job["status"] == JOB_STATUS_FAILED:
            db.update_download_status(
                paper_id,
                STATUS_FAILED,
                error=job["last_error"],
            )
        else:
            db.update_download_status(
                paper_id,
                STATUS_DOWNLOADING,
                error="Worker interrupted; download has been queued again.",
            )
    return recovered


def _heartbeat_interval(lease_seconds: int) -> float:
    return min(max(lease_seconds / 3, 0.25), 30.0)


@contextmanager
def _maintain_job_lease(
    job_id: int,
    worker_id: str,
    lease_seconds: int,
    *,
    heartbeat_interval_seconds: float | None = None,
) -> Iterator[None]:
    if lease_seconds < 2:
        raise ValueError("heartbeat lease_seconds must be at least 2")
    interval = (
        heartbeat_interval_seconds
        if heartbeat_interval_seconds is not None
        else _heartbeat_interval(lease_seconds)
    )
    if interval <= 0:
        raise ValueError("heartbeat_interval_seconds must be greater than 0")
    if interval >= lease_seconds:
        raise ValueError("heartbeat interval must be shorter than the lease")

    stop = Event()

    def heartbeat() -> None:
        while not stop.wait(interval):
            try:
                renewed = db.renew_background_job_lease(
                    job_id,
                    worker_id,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                continue
            if not renewed:
                return

    thread = Thread(
        target=heartbeat,
        name=f"job-lease-heartbeat-{job_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def _execute_claimed_job(
    job: dict,
    worker_id: str,
    retry_delay_seconds: int,
    active_handlers: dict[str, JobHandler],
) -> dict:
    handler = active_handlers.get(job["job_type"])
    try:
        if handler is None:
            raise RuntimeError(f"Unsupported background job: {job['job_type']}")
        handler(job["payload"])
    except Exception as exc:
        error = str(exc).strip() or exc.__class__.__name__
        attempt = int(job["attempt_count"])
        delay = retry_delay_seconds * (2 ** max(attempt - 1, 0))
        next_status = db.fail_background_job(
            int(job["id"]),
            worker_id,
            error,
            retry_delay_seconds=delay,
        )
        if next_status is None:
            raise RuntimeError(
                f"Background job lease was lost while recording failure: {error}"
            ) from exc
        if next_status and job["job_type"] == JOB_DOWNLOAD_PAPER and job.get("paper_id"):
            paper_id = int(job["paper_id"])
            if next_status == JOB_STATUS_RETRY_WAIT:
                db.update_download_status(
                    paper_id,
                    STATUS_DOWNLOADING,
                    error=(
                        f"Download attempt {attempt} failed; waiting for retry: {error}"
                    )[:500],
                )
            else:
                db.update_download_status(
                    paper_id,
                    STATUS_FAILED,
                    error=error[:500],
                )
        elif job["job_type"] == JOB_PREPARE_PAPER and job.get("paper_id"):
            if next_status == JOB_STATUS_RETRY_WAIT:
                _mark_preparation_retry_pending(int(job["paper_id"]), error)
            elif next_status == JOB_STATUS_FAILED:
                _mark_interrupted_preparation_failed(int(job["paper_id"]), error)
        elif (
            job["job_type"] in {JOB_PARSE_PAPER, JOB_INDEX_PAPER}
            and job.get("paper_id")
        ):
            if next_status == JOB_STATUS_RETRY_WAIT:
                _mark_stage_job_retry_pending(
                    job["job_type"],
                    int(job["paper_id"]),
                    error,
                )
            elif next_status == JOB_STATUS_FAILED:
                _mark_stage_job_failed(
                    job["job_type"],
                    int(job["paper_id"]),
                    error,
                )
        elif job["job_type"] == JOB_ANSWER_RAG_QUERY and job.get("rag_query_id"):
            query_id = int(job["rag_query_id"])
            if next_status == JOB_STATUS_RETRY_WAIT:
                _mark_rag_job_retry_pending(query_id, error)
            elif next_status == JOB_STATUS_FAILED:
                _mark_rag_job_failed(query_id, error)
        elif job["job_type"] == JOB_RESEARCH_SEARCH and job.get(
            "research_task_id"
        ):
            task_id = int(job["research_task_id"])
            if next_status == JOB_STATUS_RETRY_WAIT:
                _mark_research_search_retry_pending(task_id, error)
            elif next_status == JOB_STATUS_FAILED:
                _mark_research_search_failed(task_id, error)
        elif job["job_type"] == JOB_RESEARCH_REPORT and job.get(
            "research_task_id"
        ):
            task_id = int(job["research_task_id"])
            if next_status == JOB_STATUS_RETRY_WAIT:
                _mark_research_report_retry_pending(task_id, error)
            elif next_status == JOB_STATUS_FAILED:
                _mark_research_report_failed(task_id, error)
        job["status"] = next_status or job["status"]
        job["last_error"] = error
        return job

    if not db.complete_background_job(int(job["id"]), worker_id):
        raise RuntimeError("Background job lease was lost before completion")
    job["status"] = "succeeded"
    return job


def run_one_job(
    worker_id: str,
    *,
    lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
    retry_delay_seconds: int = 5,
    heartbeat_interval_seconds: float | None = None,
    handlers: dict[str, JobHandler] | None = None,
) -> dict | None:
    active_handlers = handlers or JOB_HANDLERS
    job = db.claim_next_background_job(
        worker_id,
        lease_seconds=lease_seconds,
        job_types=active_handlers.keys(),
    )
    if job is None:
        return None

    with _maintain_job_lease(
        int(job["id"]),
        worker_id,
        lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    ):
        return _execute_claimed_job(
            job,
            worker_id,
            retry_delay_seconds,
            active_handlers,
        )

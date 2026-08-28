import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import wraps
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db
from my_agent_project.mysql_migrations import apply_mysql_migrations


MYSQL_INTEGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


@contextmanager
def _mysql_download_claim_database():
    database_name = f"paper_hunter_phase4d2_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_phase4d2_[0-9a-f]{32}", database_name)
    admin = pymysql.connect(
        host=config.BUSINESS_DB_HOST,
        port=config.BUSINESS_DB_PORT,
        user=config.BUSINESS_DB_USER,
        password=config.BUSINESS_DB_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=config.BUSINESS_DB_CONNECT_TIMEOUT,
        read_timeout=config.BUSINESS_DB_READ_TIMEOUT,
        write_timeout=config.BUSINESS_DB_WRITE_TIMEOUT,
    )
    original_backend = db.BUSINESS_DB_BACKEND
    original_name = db.BUSINESS_DB_NAME
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        created = True
        db.BUSINESS_DB_BACKEND = "mysql"
        db.BUSINESS_DB_NAME = database_name
        connection = db.connect()
        try:
            apply_mysql_migrations(connection)
        finally:
            connection.close()
        yield
    finally:
        db.BUSINESS_DB_BACKEND = original_backend
        db.BUSINESS_DB_NAME = original_name
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin.close()


def isolated_mysql_download_claim_test(function):
    @pytest.mark.skipif(
        not MYSQL_INTEGRATION_ENABLED,
        reason="set PAPER_HUNTER_MYSQL_TEST=1 to run MySQL download claim tests",
    )
    @wraps(function)
    def run_in_isolated_database(*args, **kwargs):
        with _mysql_download_claim_database():
            return function(*args, **kwargs)

    return run_in_isolated_database


def _create_paper(*, status: str = "available", error: str | None = None) -> int:
    with db.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO papers (
                query, title, authors, summary, published, source,
                publisher, page_url, pdf_url, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "download claim",
                f"Claim Paper {uuid4().hex}",
                "Test Author",
                "Summary",
                "2026-08-28",
                "test",
                "Test Publisher",
                "https://example.test/paper",
                "https://example.test/paper.pdf",
                status,
                error,
            ),
        )
    return int(cursor.lastrowid)


def _create_selected_research_task() -> int:
    with db.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO research_tasks (
                question, max_results, plan_goal, plan_queries_json,
                planner_status, status, selected_paper_ids_json, selected_count
            )
            VALUES (?, 5, ?, ?, 'completed', 'papers_selected', ?, 1)
            """,
            ("report blocker scan", "goal", '["query"]', "[1]"),
        )
    return int(cursor.lastrowid)


def _concurrent_claims(paper_id: int, workers: int) -> list[bool]:
    barrier = threading.Barrier(workers)

    def claim_once() -> bool:
        barrier.wait()
        return db.claim_download(paper_id, ("available", "failed"))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda _index: claim_once(), range(workers)))


@isolated_mysql_download_claim_test
def test_mysql_two_workers_claim_one_paper_once() -> None:
    paper_id = _create_paper()

    claims = _concurrent_claims(paper_id, 2)

    assert claims.count(True) == 1
    assert claims.count(False) == 1
    assert db.get_paper(paper_id)["status"] == "downloading"


@isolated_mysql_download_claim_test
def test_mysql_eight_workers_claim_one_paper_once() -> None:
    paper_id = _create_paper(status="failed", error="old failure")

    claims = _concurrent_claims(paper_id, 8)

    assert claims.count(True) == 1
    assert claims.count(False) == 7
    paper = db.get_paper(paper_id)
    assert paper["status"] == "downloading"
    assert paper["error"] is None


@isolated_mysql_download_claim_test
def test_mysql_download_claim_rejects_illegal_states() -> None:
    downloaded_id = _create_paper(status="downloaded")
    link_only_id = _create_paper(status="link_only")

    assert db.claim_download(downloaded_id, ("available", "failed")) is False
    assert db.claim_download(link_only_id, ("available", "failed")) is False
    assert db.get_paper(downloaded_id)["status"] == "downloaded"
    assert db.get_paper(link_only_id)["status"] == "link_only"


@isolated_mysql_download_claim_test
def test_mysql_download_claim_stale_state_cannot_claim_again() -> None:
    paper_id = _create_paper()

    assert db.claim_download(paper_id, ("available",)) is True
    assert db.claim_download(paper_id, ("available",)) is False
    assert db.get_paper(paper_id)["status"] == "downloading"


@isolated_mysql_download_claim_test
def test_mysql_start_research_report_blocker_is_closed() -> None:
    task_id = _create_selected_research_task()

    assert db.start_research_report(task_id) is True
    assert db.start_research_report(task_id) is False
    assert db.get_research_report(task_id)["status"] == "preparing"

    db.update_research_report(task_id, "completed", content="done")
    assert db.start_research_report(task_id) is True
    assert db.get_research_report(task_id)["status"] == "preparing"


@isolated_mysql_download_claim_test
def test_mysql_conversation_summary_compare_and_swap() -> None:
    task_id = _create_selected_research_task()
    barrier = threading.Barrier(2)

    def update_once(message_id: int) -> bool:
        barrier.wait()
        return db.update_research_conversation_summary(
            task_id,
            f"summary-{message_id}",
            message_id,
            expected_previous_message_id=0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update_once, (10, 20)))

    assert results.count(True) == 1
    assert results.count(False) == 1
    summary = db.get_research_conversation_summary(task_id)
    winning_id = int(summary["summary_up_to_message_id"])
    assert winning_id in {10, 20}
    assert summary["conversation_summary"] == f"summary-{winning_id}"

import os
import re
from contextlib import contextmanager
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db, paper_summary_service, task_queue_service
from my_agent_project.mysql_migrations import apply_mysql_migrations
from my_agent_project.schemas import PaperCandidate, PaperChunk


MYSQL_INTEGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


@contextmanager
def _isolated_mysql_database():
    database_name = f"paper_hunter_summary_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_summary_[0-9a-f]{32}", database_name)
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
            assert apply_mysql_migrations(connection) == ["001", "002", "003"]
        finally:
            connection.close()
        yield database_name
    finally:
        db.BUSINESS_DB_BACKEND = original_backend
        db.BUSINESS_DB_NAME = original_name
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin.close()


@pytest.mark.skipif(
    not MYSQL_INTEGRATION_ENABLED,
    reason="set PAPER_HUNTER_MYSQL_TEST=1 to run the MySQL Paper Summary test",
)
def test_mysql_paper_summary_artifact_enqueue_worker_and_regenerate(monkeypatch):
    with _isolated_mysql_database():
        paper_id = db.insert_papers(
            "summary",
            [
                PaperCandidate(
                    title="MySQL Summary Paper",
                    authors="Author",
                    summary="metadata",
                    published="2026",
                    source="arxiv",
                    publisher="arXiv",
                    doi=None,
                    page_url="https://example.test/paper",
                    pdf_url="https://example.test/paper.pdf",
                    status="downloaded",
                )
            ],
        )[0]
        task_id = db.create_research_task(
            question="research",
            max_results=5,
            plan_goal="read",
            plan_queries=["paper"],
            plan_rationale="test",
            plan_model="test",
            planner_status="planned",
            planner_error="",
            status="awaiting_confirmation",
        )
        assert db.start_research_task(task_id)
        db.save_research_task_results(task_id, [paper_id])
        assert db.select_research_task_papers(task_id, [paper_id])
        db.get_or_create_document(paper_id)
        db.replace_paper_chunks(
            paper_id,
            [
                PaperChunk(1, 0, "The paper introduces the problem.", section_title="Introduction"),
                PaperChunk(3, 1, "The paper describes the method.", section_title="Method"),
                PaperChunk(7, 2, "The paper reports the result.", section_title="Results"),
                PaperChunk(8, 3, "The paper states its conclusion.", section_title="Conclusion"),
            ],
            page_count=8,
        )
        db.update_parse_status(paper_id, "parsed", page_count=8, chunk_count=4)
        db.update_index_status(paper_id, "indexed", vectorizer="mysql-summary-test")
        monkeypatch.setattr(
            paper_summary_service,
            "ask_deepseek",
            lambda *_args: (
                "MySQL Summary Paper method [MySQL Summary Paper, p. 3].",
                "deepseek-summary-test",
            ),
        )

        first = db.enqueue_paper_summary_job(task_id, paper_id)
        duplicate = db.enqueue_paper_summary_job(task_id, paper_id)
        result = task_queue_service.run_one_job("mysql-summary-worker")
        completed = db.get_paper_summary(task_id, paper_id)
        regenerated = db.enqueue_paper_summary_job(task_id, paper_id)

        assert first["created"] is True
        assert duplicate["created"] is False
        assert duplicate["job_id"] == first["job_id"]
        assert result["status"] == "succeeded"
        assert completed["status"] == "completed"
        assert completed["content"].endswith("[MySQL Summary Paper，第3页].")
        assert len(completed["evidence"]) == 4
        assert regenerated["created"] is True
        assert regenerated["summary_id"] == first["summary_id"]
        assert regenerated["job_id"] != first["job_id"]

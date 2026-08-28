import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db
from my_agent_project.mysql_migrations import (
    apply_mysql_migrations,
    discover_mysql_migrations,
)


MYSQL_INTEGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


@contextmanager
def _mysql_performance_database(*, initial_only: bool = False):
    database_name = f"paper_hunter_phase5_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_phase5_[0-9a-f]{32}", database_name)
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
            migrations = discover_mysql_migrations()
            apply_mysql_migrations(
                connection,
                migrations[:1] if initial_only else migrations,
            )
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


def _seed_representative_data() -> dict[str, int | str]:
    base_time = datetime(2026, 8, 28, 0, 0)
    with db.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO research_tasks (
                question, max_results, plan_goal, plan_queries_json,
                planner_status, status, created_at, updated_at
            )
            VALUES (?, 10, 'goal', '["query"]', 'completed', ?, ?, ?)
            """,
            [
                (
                    f"task-{index}",
                    "papers_selected" if index == 0 else "completed",
                    base_time + timedelta(seconds=index),
                    base_time + timedelta(seconds=index),
                )
                for index in range(200)
            ],
        )
        task_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM research_tasks ORDER BY id"
            ).fetchall()
        ]
        hot_task_id = task_ids[0]
        hot_messages = [
            (
                hot_task_id,
                "user" if index % 2 == 0 else "assistant",
                "followup" if index >= 100 else "planning",
                f"message-{index} " + ("content " * 20),
                "completed" if index % 17 else "failed",
                base_time + timedelta(seconds=index),
                base_time + timedelta(seconds=index),
            )
            for index in range(2000)
        ]
        other_messages = [
            (
                task_ids[1 + (index % (len(task_ids) - 1))],
                "user" if index % 2 == 0 else "assistant",
                "followup",
                f"other-message-{index} " + ("content " * 10),
                "completed",
                base_time + timedelta(seconds=2000 + index),
                base_time + timedelta(seconds=2000 + index),
            )
            for index in range(8000)
        ]

        connection.executemany(
            """
            INSERT INTO papers (
                query, title, authors, summary, published, source,
                publisher, page_url, pdf_url, status, created_at, updated_at
            )
            VALUES (?, ?, 'author', ?, '2026-08-28', 'test',
                    'publisher', ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"query-{index % 20}",
                    f"paper-{index}",
                    "summary " * 20,
                    f"https://example.test/{index}",
                    f"https://example.test/{index}.pdf",
                    "available" if index % 4 else "downloaded",
                    base_time + timedelta(seconds=index),
                    base_time + timedelta(seconds=index),
                )
                for index in range(300)
            ],
        )
        paper_ids = [
            int(row["id"])
            for row in connection.execute("SELECT id FROM papers ORDER BY id").fetchall()
        ]
        hot_paper_id = paper_ids[0]
        connection.executemany(
            "INSERT INTO paper_documents (paper_id, parse_status) VALUES (?, ?)",
            [(paper_id, "parsed") for paper_id in paper_ids],
        )
        connection.executemany(
            """
            INSERT INTO paper_parent_chunks (
                paper_id, page_number, parent_index, content
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (hot_paper_id, index // 5 + 1, index, f"parent-{index}")
                for index in range(100)
            ],
        )
        parent_ids = [
            int(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM paper_parent_chunks
                WHERE paper_id = ? ORDER BY parent_index
                """,
                (hot_paper_id,),
            ).fetchall()
        ]
        connection.executemany(
            """
            INSERT INTO paper_chunks (
                paper_id, page_number, chunk_index, content,
                parent_chunk_id, vector_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    hot_paper_id,
                    index // 5 + 1,
                    index,
                    f"chunk-{index} " + ("content " * 30),
                    parent_ids[index % len(parent_ids)],
                    '[0.1, 0.2]',
                )
                for index in range(1000)
            ],
        )
        connection.executemany(
            """
            INSERT INTO paper_chunks (
                paper_id, page_number, chunk_index, content, vector_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    paper_id,
                    index // 5 + 1,
                    index,
                    f"cold-chunk-{paper_id}-{index} " + ("content " * 10),
                    '[0.1, 0.2]',
                )
                for paper_id in paper_ids[1:]
                for index in range(30)
            ],
        )
        connection.executemany(
            """
            INSERT INTO rag_queries (
                paper_id, question, status, created_at, updated_at
            )
            VALUES (?, ?, 'completed', ?, ?)
            """,
            [
                (
                    hot_paper_id,
                    f"question-{index}",
                    base_time + timedelta(seconds=index),
                    base_time + timedelta(seconds=index),
                )
                for index in range(200)
            ],
        )
        connection.executemany(
            """
            INSERT INTO rag_queries (
                paper_id, question, status, created_at, updated_at
            )
            VALUES (?, ?, 'completed', ?, ?)
            """,
            [
                (
                    paper_id,
                    f"cold-question-{paper_id}-{index}",
                    base_time + timedelta(seconds=index),
                    base_time + timedelta(seconds=index),
                )
                for paper_id in paper_ids[1:]
                for index in range(10)
            ],
        )
        connection.executemany(
            """
            INSERT INTO research_tool_calls (
                task_id, call_index, tool_name, input_json, status
            )
            VALUES (?, ?, 'search_literature', '{}', 'success')
            """,
            [(hot_task_id, index + 1) for index in range(20)],
        )
        connection.executemany(
            """
            INSERT INTO research_tool_calls (
                task_id, call_index, tool_name, input_json, status
            )
            VALUES (?, ?, 'search_literature', '{}', 'success')
            """,
            [
                (task_id, index + 1)
                for task_id in task_ids[1:]
                for index in range(10)
            ],
        )
        connection.execute(
            """
            INSERT INTO research_reports (task_id, status, content)
            VALUES (?, 'completed', 'report')
            """,
            (hot_task_id,),
        )

        connection.executemany(
            """
            INSERT INTO research_messages (
                task_id, role, phase, content, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [*hot_messages, *other_messages],
        )
        connection.executemany(
            """
            INSERT INTO research_task_memories (
                research_task_id, category, content, status,
                created_from_message_id, updated_from_message_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    hot_task_id,
                    f"category-{index % 4}",
                    f"memory-{index}",
                    "active" if index < 80 else "removed",
                    index + 1,
                    index + 1,
                )
                for index in range(100)
            ],
        )

        job_types = (
            "download_paper",
            "prepare_paper",
            "parse_paper",
            "index_paper",
            "research_search",
            "research_report",
            "research_followup",
        )
        jobs = []
        for index in range(9830):
            if index < 9500:
                status = "succeeded" if index % 3 else "failed"
                available_at = base_time - timedelta(days=1)
                lease_expires_at = None
                worker_id = None
            elif index < 9700:
                status = "queued"
                available_at = (
                    base_time - timedelta(seconds=1)
                    if index % 2
                    else base_time + timedelta(days=1)
                )
                lease_expires_at = None
                worker_id = None
            elif index < 9800:
                status = "retry_wait"
                available_at = (
                    base_time - timedelta(seconds=1)
                    if index % 2
                    else base_time + timedelta(days=1)
                )
                lease_expires_at = None
                worker_id = None
            else:
                status = "running"
                available_at = base_time - timedelta(minutes=1)
                lease_expires_at = (
                    base_time - timedelta(seconds=1)
                    if index % 2
                    else base_time + timedelta(minutes=5)
                )
                worker_id = f"worker-{index % 8}"
            job_type = job_types[index % len(job_types)]
            jobs.append(
                (
                    job_type,
                    '{"test":true}',
                    f"phase5-job-{index}",
                    paper_ids[index % len(paper_ids)],
                    task_ids[index % len(task_ids)],
                    status,
                    index % 10,
                    available_at,
                    lease_expires_at,
                    worker_id,
                    base_time + timedelta(seconds=index),
                    base_time + timedelta(seconds=index),
                )
            )
        connection.executemany(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, paper_id,
                research_task_id, status, priority, available_at,
                lease_expires_at, worker_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            jobs,
        )
        for table_name in (
            "background_jobs",
            "research_messages",
            "research_task_memories",
            "papers",
            "paper_documents",
            "paper_parent_chunks",
            "paper_chunks",
            "rag_queries",
            "research_tool_calls",
        ):
            connection.execute(f"ANALYZE TABLE {table_name}").fetchall()
    return {
        "hot_task_id": hot_task_id,
        "hot_paper_id": hot_paper_id,
        "active_dedupe_key": "phase5-job-9501",
    }


def _explain(sql: str, params=()) -> dict:
    with db.read_connection() as connection:
        rows = connection.execute("EXPLAIN " + sql, params).fetchall()
    normalized_rows = [
        {str(key).lower(): value for key, value in row.items()} for row in rows
    ]
    return {
        "type": normalized_rows[0].get("type"),
        "possible_keys": normalized_rows[0].get("possible_keys"),
        "key": normalized_rows[0].get("key"),
        "key_len": normalized_rows[0].get("key_len"),
        "rows": int(normalized_rows[0].get("rows") or 0),
        "filtered": float(normalized_rows[0].get("filtered") or 0),
        "extra": normalized_rows[0].get("extra") or "",
        "plan": normalized_rows,
    }


@pytest.mark.skipif(
    not MYSQL_INTEGRATION_ENABLED,
    reason="set PAPER_HUNTER_MYSQL_TEST=1 to run MySQL performance tests",
)
def test_mysql_representative_explain_probe() -> None:
    with _mysql_performance_database(initial_only=True):
        seeded = _seed_representative_data()
        now = datetime(2026, 8, 28, 0, 0)
        plans = {
            "claim_without_type": _explain(
                """
                SELECT id FROM background_jobs
                WHERE status IN ('queued', 'retry_wait') AND available_at <= ?
                ORDER BY priority DESC, id LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (now,),
            ),
            "claim_with_type": _explain(
                """
                SELECT id FROM background_jobs
                WHERE status IN ('queued', 'retry_wait') AND available_at <= ?
                  AND job_type IN (?)
                ORDER BY priority DESC, id LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (now, "download_paper"),
            ),
            "active_dedupe_current": _explain(
                """
                SELECT id, job_type, paper_id, rag_query_id, research_task_id
                FROM background_jobs
                WHERE dedupe_key = ?
                  AND status IN ('queued', 'running', 'retry_wait')
                LIMIT 1
                """,
                (seeded["active_dedupe_key"],),
            ),
            "active_dedupe_generated": _explain(
                """
                SELECT id, job_type, paper_id, rag_query_id, research_task_id
                FROM background_jobs
                WHERE active_dedupe_key = ? LIMIT 1
                """,
                (seeded["active_dedupe_key"],),
            ),
            "expired_recovery": _explain(
                """
                SELECT * FROM background_jobs
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                """,
                (now,),
            ),
            "recent_messages": _explain(
                """
                SELECT * FROM research_messages
                WHERE task_id = ? AND phase = 'followup'
                  AND role IN ('user', 'assistant') AND status = 'completed'
                ORDER BY created_at DESC, id DESC LIMIT 10
                """,
                (seeded["hot_task_id"],),
            ),
            "active_memories": _explain(
                """
                SELECT * FROM research_task_memories
                WHERE research_task_id = ? AND status = 'active'
                ORDER BY id ASC
                """,
                (seeded["hot_task_id"],),
            ),
            "task_parent_lock": _explain(
                "SELECT id FROM research_tasks WHERE id = ? FOR UPDATE",
                (seeded["hot_task_id"],),
            ),
            "paper_download_cas": _explain(
                """
                UPDATE papers SET status = 'downloading'
                WHERE id = ? AND status IN ('available', 'failed')
                """,
                (seeded["hot_paper_id"],),
            ),
            "document_by_paper": _explain(
                "SELECT * FROM paper_documents WHERE paper_id = ?",
                (seeded["hot_paper_id"],),
            ),
            "task_job_recent": _explain(
                """
                SELECT status FROM background_jobs
                WHERE research_task_id = ? AND job_type = 'research_search'
                ORDER BY id DESC LIMIT 1
                """,
                (seeded["hot_task_id"],),
            ),
            "paper_job_recent": _explain(
                """
                SELECT status FROM background_jobs
                WHERE paper_id = ? AND job_type = 'download_paper'
                ORDER BY id DESC LIMIT 1
                """,
                (seeded["hot_paper_id"],),
            ),
            "indexed_chunks": _explain(
                """
                SELECT c.*, p.page_number AS parent_page_number,
                       p.parent_index, p.content AS parent_content
                FROM paper_chunks AS c
                LEFT JOIN paper_parent_chunks AS p ON p.id = c.parent_chunk_id
                WHERE c.paper_id = ? AND c.vector_json IS NOT NULL
                ORDER BY c.chunk_index
                """,
                (seeded["hot_paper_id"],),
            ),
            "recent_rag_queries": _explain(
                """
                SELECT * FROM rag_queries
                WHERE paper_id = ? ORDER BY id DESC LIMIT 20
                """,
                (seeded["hot_paper_id"],),
            ),
            "task_tool_calls": _explain(
                """
                SELECT * FROM research_tool_calls
                WHERE task_id = ? ORDER BY call_index
                """,
                (seeded["hot_task_id"],),
            ),
            "report_by_task": _explain(
                "SELECT * FROM research_reports WHERE task_id = ?",
                (seeded["hot_task_id"],),
            ),
        }
        print(
            "PHASE5_EXPLAIN_BEFORE="
            + json.dumps(plans, ensure_ascii=False, sort_keys=True)
        )
        connection = db.connect()
        try:
            assert apply_mysql_migrations(connection) == ["002"]
            assert apply_mysql_migrations(connection) == []
            with connection.cursor() as cursor:
                cursor.execute("ANALYZE TABLE research_messages")
                cursor.fetchall()
                cursor.execute("ANALYZE TABLE background_jobs")
                cursor.fetchall()
        finally:
            connection.close()
        after_plans = {
            "claim_without_type": _explain(
                """
                SELECT id FROM background_jobs
                WHERE status IN ('queued', 'retry_wait') AND available_at <= ?
                ORDER BY priority DESC, id LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (now,),
            ),
            "claim_with_type": _explain(
                """
                SELECT id FROM background_jobs
                WHERE status IN ('queued', 'retry_wait') AND available_at <= ?
                  AND job_type IN (?)
                ORDER BY priority DESC, id LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (now, "download_paper"),
            ),
            "active_dedupe": _explain(
                """
                SELECT id, job_type, paper_id, rag_query_id, research_task_id
                FROM background_jobs
                WHERE active_dedupe_key = ? LIMIT 1
                """,
                (seeded["active_dedupe_key"],),
            ),
            "recent_messages": _explain(
                """
                SELECT * FROM research_messages
                WHERE task_id = ? AND phase = 'followup'
                  AND role IN ('user', 'assistant') AND status = 'completed'
                ORDER BY created_at DESC, id DESC LIMIT 10
                """,
                (seeded["hot_task_id"],),
            ),
            "expired_recovery": _explain(
                """
                SELECT * FROM background_jobs
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                """,
                (now,),
            ),
            "task_job_recent": _explain(
                """
                SELECT status FROM background_jobs
                WHERE research_task_id = ? AND job_type = 'research_search'
                ORDER BY id DESC LIMIT 1
                """,
                (seeded["hot_task_id"],),
            ),
            "paper_job_recent": _explain(
                """
                SELECT status FROM background_jobs
                WHERE paper_id = ? AND job_type = 'download_paper'
                ORDER BY id DESC LIMIT 1
                """,
                (seeded["hot_paper_id"],),
            ),
            "indexed_chunks": _explain(
                """
                SELECT c.*, p.page_number AS parent_page_number,
                       p.parent_index, p.content AS parent_content
                FROM paper_chunks AS c
                LEFT JOIN paper_parent_chunks AS p ON p.id = c.parent_chunk_id
                WHERE c.paper_id = ? AND c.vector_json IS NOT NULL
                ORDER BY c.chunk_index
                """,
                (seeded["hot_paper_id"],),
            ),
            "recent_rag_queries": _explain(
                """
                SELECT * FROM rag_queries
                WHERE paper_id = ? ORDER BY id DESC LIMIT 20
                """,
                (seeded["hot_paper_id"],),
            ),
            "task_tool_calls": _explain(
                """
                SELECT * FROM research_tool_calls
                WHERE task_id = ? ORDER BY call_index
                """,
                (seeded["hot_task_id"],),
            ),
            "report_by_task": _explain(
                "SELECT * FROM research_reports WHERE task_id = ?",
                (seeded["hot_task_id"],),
            ),
            "active_memories": _explain(
                """
                SELECT * FROM research_task_memories
                WHERE research_task_id = ? AND status = 'active'
                ORDER BY id ASC
                """,
                (seeded["hot_task_id"],),
            ),
            "task_parent_lock": _explain(
                "SELECT id FROM research_tasks WHERE id = ? FOR UPDATE",
                (seeded["hot_task_id"],),
            ),
            "paper_download_cas": _explain(
                """
                UPDATE papers SET status = 'downloading'
                WHERE id = ? AND status IN ('available', 'failed')
                """,
                (seeded["hot_paper_id"],),
            ),
            "document_by_paper": _explain(
                "SELECT * FROM paper_documents WHERE paper_id = ?",
                (seeded["hot_paper_id"],),
            ),
        }
        print(
            "PHASE5_EXPLAIN_AFTER="
            + json.dumps(after_plans, ensure_ascii=False, sort_keys=True)
        )
        assert set(plans) == {
            "claim_without_type",
            "claim_with_type",
            "active_dedupe_current",
            "active_dedupe_generated",
            "expired_recovery",
            "recent_messages",
            "active_memories",
            "task_parent_lock",
            "paper_download_cas",
            "document_by_paper",
            "task_job_recent",
            "paper_job_recent",
            "indexed_chunks",
            "recent_rag_queries",
            "task_tool_calls",
            "report_by_task",
        }
        assert plans["claim_without_type"]["key"] == "idx_background_jobs_ready"
        assert plans["claim_without_type"]["rows"] <= 300
        assert plans["claim_with_type"]["key"] == "idx_background_jobs_ready"
        assert plans["claim_with_type"]["rows"] <= 300
        assert plans["expired_recovery"]["key"] == "idx_background_jobs_ready"
        assert plans["expired_recovery"]["rows"] <= 50
        assert plans["active_dedupe_current"]["rows"] >= 100
        assert plans["active_dedupe_generated"]["type"] == "const"
        assert plans["active_dedupe_generated"]["key"] == (
            "idx_background_jobs_active_dedupe"
        )
        assert plans["active_dedupe_generated"]["rows"] == 1
        assert "Using filesort" in plans["recent_messages"]["extra"]
        assert plans["active_memories"]["key"] == (
            "idx_research_task_memories_task_status"
        )
        assert plans["task_parent_lock"]["key"] == "PRIMARY"
        assert plans["paper_download_cas"]["key"] == "PRIMARY"
        assert plans["paper_download_cas"]["rows"] == 1
        assert plans["document_by_paper"]["key"] == "PRIMARY"
        assert plans["indexed_chunks"]["key"] == (
            "uq_paper_chunks_paper_index"
        )
        assert "Using filesort" not in plans["indexed_chunks"]["extra"]
        assert plans["recent_rag_queries"]["key"] == "idx_rag_queries_paper_id"
        assert plans["task_tool_calls"]["key"] == (
            "uq_research_tool_calls_task_call"
        )
        assert plans["report_by_task"]["key"] == "PRIMARY"
        assert after_plans["active_dedupe"]["type"] == "const"
        assert after_plans["active_dedupe"]["rows"] == 1
        assert after_plans["recent_messages"]["key"] == (
            "idx_research_messages_followup_recent"
        )
        assert "Using filesort" not in after_plans["recent_messages"]["extra"]
        assert after_plans["task_job_recent"]["key"] == (
            "idx_background_jobs_task_type_recent"
        )
        assert after_plans["task_job_recent"]["rows"] <= 10
        assert after_plans["paper_job_recent"]["key"] == (
            "idx_background_jobs_paper_type_recent"
        )
        assert after_plans["paper_job_recent"]["rows"] <= 10
        assert after_plans["claim_without_type"]["key"] == (
            "idx_background_jobs_ready"
        )
        assert after_plans["expired_recovery"]["key"] == (
            "idx_background_jobs_ready"
        )

        with db.read_connection() as connection:
            index_rows = connection.execute(
                """
                SELECT INDEX_NAME AS index_name
                FROM information_schema.statistics
                WHERE table_schema = ?
                  AND table_name IN ('background_jobs', 'research_messages')
                """,
                (db.BUSINESS_DB_NAME,),
            ).fetchall()
            foreign_key_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM information_schema.referential_constraints
                    WHERE constraint_schema = ?
                    """,
                    (db.BUSINESS_DB_NAME,),
                ).fetchone()["count"]
            )
        index_names = {row["index_name"] for row in index_rows}
        assert "idx_background_jobs_recovery" not in index_names
        assert "idx_background_jobs_ready" in index_names
        assert "idx_background_jobs_active_dedupe" in index_names
        assert "idx_background_jobs_task_type_recent" in index_names
        assert "idx_background_jobs_paper_type_recent" in index_names
        assert "idx_research_messages_followup_recent" in index_names
        assert foreign_key_count == 19

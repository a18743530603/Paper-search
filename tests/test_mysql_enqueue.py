import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
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
def _mysql_enqueue_database():
    database_name = f"paper_hunter_phase4c_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_phase4c_[0-9a-f]{32}", database_name)
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


def isolated_mysql_enqueue_test(function):
    @pytest.mark.skipif(
        not MYSQL_INTEGRATION_ENABLED,
        reason="set PAPER_HUNTER_MYSQL_TEST=1 to run MySQL enqueue tests",
    )
    @wraps(function)
    def run_in_isolated_database(*args, **kwargs):
        with _mysql_enqueue_database():
            return function(*args, **kwargs)

    return run_in_isolated_database


def _create_research_task(
    *,
    status: str,
    selected_count: int = 0,
) -> int:
    with db.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO research_tasks (
                question, max_results, plan_goal, plan_queries_json,
                planner_status, status, selected_paper_ids_json, selected_count
            )
            VALUES ('question', 5, 'goal', '["query"]', 'completed', ?, '[]', ?)
            """,
            (status, selected_count),
        )
    return int(cursor.lastrowid)


def _complete_report(task_id: int) -> None:
    with db.transaction() as connection:
        connection.execute(
            """
            INSERT INTO research_reports (task_id, status, content)
            VALUES (?, 'completed', 'report')
            """,
            (task_id,),
        )


def _create_paper(
    *,
    status: str = "available",
    local_path: str | None = None,
) -> int:
    with db.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO papers (
                query, title, authors, summary, published, source,
                publisher, page_url, pdf_url, local_path, status
            )
            VALUES (?, ?, 'Author', 'Summary', '2026', 'test',
                    'Publisher', ?, ?, ?, ?)
            """,
            (
                "query",
                f"Paper {uuid4().hex}",
                f"https://example.test/{uuid4().hex}",
                f"https://example.test/{uuid4().hex}.pdf",
                local_path,
                status,
            ),
        )
    return int(cursor.lastrowid)


def _create_document(
    paper_id: int,
    *,
    parse_status: str,
    index_status: str,
) -> None:
    with db.transaction() as connection:
        connection.execute(
            """
            INSERT INTO paper_documents (paper_id, parse_status, index_status)
            VALUES (?, ?, ?)
            """,
            (paper_id, parse_status, index_status),
        )


def _active_jobs(*, dedupe_key: str | None = None) -> list[dict]:
    where = ""
    params: tuple[object, ...] = ()
    if dedupe_key is not None:
        where = "AND dedupe_key = ?"
        params = (dedupe_key,)
    with db.read_connection() as connection:
        return connection.execute(
            f"""
            SELECT *
            FROM background_jobs
            WHERE status IN ('queued', 'running', 'retry_wait')
              {where}
            ORDER BY id
            """,
            params,
        ).fetchall()


def _all_jobs(dedupe_key: str) -> list[dict]:
    with db.read_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM background_jobs
            WHERE dedupe_key = ?
            ORDER BY id
            """,
            (dedupe_key,),
        ).fetchall()


def _messages(task_id: int) -> list[dict]:
    with db.read_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM research_messages
            WHERE task_id = ?
            ORDER BY id
            """,
            (task_id,),
        ).fetchall()


def _concurrent_results(count: int, operation):
    barrier = threading.Barrier(count)

    def invoke(index: int):
        barrier.wait()
        try:
            return {"kind": "result", "value": operation(index)}
        except RuntimeError as exc:
            return {"kind": "runtime_error", "value": str(exc)}

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(invoke, range(count)))


@isolated_mysql_enqueue_test
def test_mysql_two_research_search_enqueues_share_one_active_job() -> None:
    task_id = _create_research_task(status="awaiting_confirmation")
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_research_search_job(task_id),
    )
    values = [item["value"] for item in results]

    assert all(item["kind"] == "result" for item in results)
    assert {value["job_id"] for value in values} == {values[0]["job_id"]}
    assert sorted(value["created"] for value in values) == [False, True]
    assert len(_active_jobs(dedupe_key=f"research_search:{task_id}")) == 1
    assert db.get_research_task(task_id)["status"] == "running"


@isolated_mysql_enqueue_test
def test_mysql_two_research_report_enqueues_share_one_active_job() -> None:
    task_id = _create_research_task(status="papers_selected", selected_count=1)
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_research_report_job(task_id),
    )
    values = [item["value"] for item in results]

    assert all(item["kind"] == "result" for item in results)
    assert {value["job_id"] for value in values} == {values[0]["job_id"]}
    assert sorted(value["created"] for value in values) == [False, True]
    assert len(_active_jobs(dedupe_key=f"research_report:{task_id}")) == 1
    assert db.get_research_report(task_id)["status"] == "preparing"


@isolated_mysql_enqueue_test
def test_mysql_followup_messages_and_job_remain_atomic_under_duplicate() -> None:
    task_id = _create_research_task(status="papers_selected", selected_count=1)
    _complete_report(task_id)
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_research_followup_job(task_id, "Same question"),
    )

    assert [item["kind"] for item in results].count("result") == 1
    assert [item["kind"] for item in results].count("runtime_error") == 1
    jobs = _active_jobs(dedupe_key=f"research_followup:{task_id}")
    messages = _messages(task_id)
    assert len(jobs) == 1
    assert [(item["role"], item["status"]) for item in messages] == [
        ("user", "completed"),
        ("assistant", "pending"),
    ]
    payload = db.get_background_job(int(jobs[0]["id"]))["payload"]
    assert payload["user_message_id"] == messages[0]["id"]
    assert payload["message_id"] == messages[1]["id"]


@isolated_mysql_enqueue_test
def test_mysql_streaming_and_worker_followup_paths_share_task_lock() -> None:
    task_id = _create_research_task(status="papers_selected", selected_count=1)
    _complete_report(task_id)
    operations = (
        lambda: db.create_research_followup_messages(task_id, "Live question"),
        lambda: db.enqueue_research_followup_job(task_id, "Fallback question"),
    )
    barrier = threading.Barrier(2)

    def invoke(index: int):
        barrier.wait()
        try:
            return ("result", operations[index]())
        except RuntimeError as exc:
            return ("runtime_error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, range(2)))

    assert [kind for kind, _ in results].count("result") == 1
    assert [kind for kind, _ in results].count("runtime_error") == 1
    messages = _messages(task_id)
    jobs = _active_jobs(dedupe_key=f"research_followup:{task_id}")
    assert len(messages) == 2
    assert len(jobs) in {0, 1}
    if jobs:
        payload = db.get_background_job(int(jobs[0]["id"]))["payload"]
        assert payload["user_message_id"] == messages[0]["id"]
        assert payload["message_id"] == messages[1]["id"]


@isolated_mysql_enqueue_test
def test_mysql_two_download_enqueues_create_one_active_job() -> None:
    paper_id = _create_paper()
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_download_job(paper_id, ("available",)),
    )
    values = [item["value"] for item in results]

    assert all(item["kind"] == "result" for item in results)
    assert sum(value is not None for value in values) == 1
    assert len(_active_jobs(dedupe_key=f"download_paper:{paper_id}")) == 1
    assert db.get_paper(paper_id)["status"] == "downloading"


@isolated_mysql_enqueue_test
def test_mysql_two_prepare_enqueues_create_one_active_job() -> None:
    paper_id = _create_paper()
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_paper_preparation_job(paper_id),
    )

    assert [item["kind"] for item in results].count("result") == 1
    assert [item["kind"] for item in results].count("runtime_error") == 1
    assert len(_active_jobs(dedupe_key=f"prepare_paper:{paper_id}")) == 1
    assert db.get_paper(paper_id)["status"] == "downloading"


@isolated_mysql_enqueue_test
def test_mysql_two_parse_enqueues_create_one_active_job() -> None:
    paper_id = _create_paper(status="downloaded", local_path="paper.pdf")
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_parse_job(paper_id),
    )

    assert [item["kind"] for item in results].count("result") == 1
    assert [item["kind"] for item in results].count("runtime_error") == 1
    assert len(_active_jobs(dedupe_key=f"parse_paper:{paper_id}")) == 1
    assert db.get_document(paper_id)["parse_status"] == "parsing"


@isolated_mysql_enqueue_test
def test_mysql_two_index_enqueues_create_one_active_job() -> None:
    paper_id = _create_paper(status="downloaded", local_path="paper.pdf")
    _create_document(
        paper_id,
        parse_status="parsed",
        index_status="not_indexed",
    )
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_index_job(paper_id),
    )

    assert [item["kind"] for item in results].count("result") == 1
    assert [item["kind"] for item in results].count("runtime_error") == 1
    assert len(_active_jobs(dedupe_key=f"index_paper:{paper_id}")) == 1
    assert db.get_document(paper_id)["index_status"] == "indexing"


@isolated_mysql_enqueue_test
def test_mysql_two_identical_rag_enqueues_share_query_and_job() -> None:
    paper_id = _create_paper(status="downloaded", local_path="paper.pdf")
    _create_document(paper_id, parse_status="parsed", index_status="indexed")
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_rag_answer_job(paper_id, "Same question?"),
    )
    values = [item["value"] for item in results]

    assert all(item["kind"] == "result" for item in results)
    assert {value["job_id"] for value in values} == {values[0]["job_id"]}
    assert {value["query_id"] for value in values} == {values[0]["query_id"]}
    assert sorted(value["created"] for value in values) == [False, True]
    assert len(db.list_rag_queries(paper_id)) == 1
    assert len(_active_jobs()) == 1


@isolated_mysql_enqueue_test
def test_mysql_eight_search_enqueues_create_one_active_job() -> None:
    task_id = _create_research_task(status="awaiting_confirmation")
    results = _concurrent_results(
        8,
        lambda _index: db.enqueue_research_search_job(task_id),
    )
    values = [item["value"] for item in results]

    assert all(item["kind"] == "result" for item in results)
    assert len({value["job_id"] for value in values}) == 1
    assert sum(value["created"] for value in values) == 1
    assert len(_active_jobs(dedupe_key=f"research_search:{task_id}")) == 1


@isolated_mysql_enqueue_test
def test_mysql_historical_succeeded_and_failed_jobs_allow_new_active_job() -> None:
    task_id = _create_research_task(status="papers_selected", selected_count=1)
    dedupe_key = f"research_report:{task_id}"
    first = db.enqueue_research_report_job(task_id)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE background_jobs SET status = 'succeeded' WHERE id = ?",
            (first["job_id"],),
        )
        connection.execute(
            "UPDATE research_reports SET status = 'completed' WHERE task_id = ?",
            (task_id,),
        )

    second = db.enqueue_research_report_job(task_id)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE background_jobs SET status = 'failed' WHERE id = ?",
            (second["job_id"],),
        )
        connection.execute(
            "UPDATE research_reports SET status = 'failed' WHERE task_id = ?",
            (task_id,),
        )

    third = db.enqueue_research_report_job(task_id)
    jobs = _all_jobs(dedupe_key)
    assert [job["status"] for job in jobs] == ["succeeded", "failed", "queued"]
    assert third["created"] is True
    assert len({job["id"] for job in jobs}) == 3
    assert len(_active_jobs(dedupe_key=dedupe_key)) == 1


@isolated_mysql_enqueue_test
def test_mysql_paper_history_does_not_block_new_active_parse_job() -> None:
    paper_id = _create_paper(status="downloaded", local_path="paper.pdf")
    dedupe_key = f"parse_paper:{paper_id}"
    first = db.enqueue_parse_job(paper_id)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE background_jobs SET status = 'succeeded' WHERE id = ?",
            (first["job_id"],),
        )
        connection.execute(
            """
            UPDATE paper_documents
            SET parse_status = 'parsed'
            WHERE paper_id = ?
            """,
            (paper_id,),
        )

    second = db.enqueue_parse_job(paper_id)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE background_jobs SET status = 'failed' WHERE id = ?",
            (second["job_id"],),
        )
        connection.execute(
            """
            UPDATE paper_documents
            SET parse_status = 'parse_failed'
            WHERE paper_id = ?
            """,
            (paper_id,),
        )

    third = db.enqueue_parse_job(paper_id)
    jobs = _all_jobs(dedupe_key)
    assert [job["status"] for job in jobs] == ["succeeded", "failed", "queued"]
    assert third["job_id"] not in {first["job_id"], second["job_id"]}
    assert len(_active_jobs(dedupe_key=dedupe_key)) == 1


@isolated_mysql_enqueue_test
def test_mysql_exact_active_dedupe_1062_returns_committed_winner(
    monkeypatch,
) -> None:
    task_id = _create_research_task(status="papers_selected", selected_count=1)
    report_barrier = threading.Barrier(2)
    conflict_keys: list[str | None] = []
    original_execute = db.MySQLConnectionAdapter.execute
    original_conflict_reader = db._active_job_after_expected_dedupe_conflict

    def synchronized_execute(connection, sql, params=None):
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("INSERT INTO RESEARCH_REPORTS"):
            report_barrier.wait(timeout=5)
        return original_execute(connection, sql, params)

    def record_conflict(exc, dedupe_key):
        conflict_keys.append(db._mysql_duplicate_key_name(exc))
        return original_conflict_reader(exc, dedupe_key)

    monkeypatch.setattr(db, "_enqueue_lock_clause", lambda: "")
    monkeypatch.setattr(db.MySQLConnectionAdapter, "execute", synchronized_execute)
    monkeypatch.setattr(
        db,
        "_active_job_after_expected_dedupe_conflict",
        record_conflict,
    )
    results = _concurrent_results(
        2,
        lambda _index: db.enqueue_research_report_job(task_id),
    )
    values = [item["value"] for item in results]

    assert all(item["kind"] == "result" for item in results)
    assert sorted(value["created"] for value in values) == [False, True]
    assert len({value["job_id"] for value in values}) == 1
    assert conflict_keys == [db.MYSQL_ACTIVE_DEDUPE_UNIQUE_KEY]
    assert len(_active_jobs(dedupe_key=f"research_report:{task_id}")) == 1


def _fail_background_job_insert_with(error):
    original_execute = db.MySQLConnectionAdapter.execute

    def execute(connection, sql, params=None):
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("INSERT INTO BACKGROUND_JOBS"):
            raise error
        return original_execute(connection, sql, params)

    return execute


@isolated_mysql_enqueue_test
def test_mysql_search_state_rolls_back_when_job_insert_fails(monkeypatch) -> None:
    task_id = _create_research_task(status="awaiting_confirmation")
    monkeypatch.setattr(
        db.MySQLConnectionAdapter,
        "execute",
        _fail_background_job_insert_with(
            pymysql.err.IntegrityError(1048, "job_type cannot be null")
        ),
    )

    with pytest.raises(pymysql.err.IntegrityError):
        db.enqueue_research_search_job(task_id)

    assert db.get_research_task(task_id)["status"] == "awaiting_confirmation"
    assert _active_jobs() == []


@isolated_mysql_enqueue_test
def test_mysql_followup_messages_roll_back_when_job_insert_fails(
    monkeypatch,
) -> None:
    task_id = _create_research_task(status="papers_selected", selected_count=1)
    _complete_report(task_id)
    monkeypatch.setattr(
        db.MySQLConnectionAdapter,
        "execute",
        _fail_background_job_insert_with(
            pymysql.err.IntegrityError(1452, "foreign key violation")
        ),
    )

    with pytest.raises(pymysql.err.IntegrityError):
        db.enqueue_research_followup_job(task_id, "Question")

    assert _messages(task_id) == []
    assert _active_jobs() == []


@isolated_mysql_enqueue_test
def test_mysql_paper_state_rolls_back_when_job_insert_fails(monkeypatch) -> None:
    paper_id = _create_paper()
    monkeypatch.setattr(
        db.MySQLConnectionAdapter,
        "execute",
        _fail_background_job_insert_with(
            pymysql.err.DataError(1406, "payload too long")
        ),
    )

    with pytest.raises(pymysql.err.DataError):
        db.enqueue_download_job(paper_id, ("available",))

    assert db.get_paper(paper_id)["status"] == "available"
    assert _active_jobs() == []


@isolated_mysql_enqueue_test
def test_mysql_unrelated_1062_is_not_treated_as_active_dedupe(
    monkeypatch,
) -> None:
    task_id = _create_research_task(status="awaiting_confirmation")
    unrelated = pymysql.err.IntegrityError(
        1062,
        "Duplicate entry 'x' for key 'background_jobs.unrelated_unique'",
    )
    monkeypatch.setattr(
        db.MySQLConnectionAdapter,
        "execute",
        _fail_background_job_insert_with(unrelated),
    )

    with pytest.raises(pymysql.err.IntegrityError) as captured:
        db.enqueue_research_search_job(task_id)

    assert captured.value.args[0] == 1062
    assert db.get_research_task(task_id)["status"] == "awaiting_confirmation"
    assert _active_jobs() == []

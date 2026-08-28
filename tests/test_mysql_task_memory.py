import os
import re
import threading
import time
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
def _mysql_task_memory_database():
    database_name = f"paper_hunter_phase4d1_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_phase4d1_[0-9a-f]{32}", database_name)
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


def isolated_mysql_task_memory_test(function):
    @pytest.mark.skipif(
        not MYSQL_INTEGRATION_ENABLED,
        reason="set PAPER_HUNTER_MYSQL_TEST=1 to run MySQL Task Memory tests",
    )
    @wraps(function)
    def run_in_isolated_database(*args, **kwargs):
        with _mysql_task_memory_database():
            return function(*args, **kwargs)

    return run_in_isolated_database


def _create_task() -> int:
    with db.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO research_tasks (
                question, max_results, plan_goal, plan_queries_json,
                planner_status, status
            )
            VALUES ('memory question', 5, 'goal', '["query"]',
                    'completed', 'papers_selected')
            """
        )
    return int(cursor.lastrowid)


def _create_source_message(task_id: int, content: str) -> int:
    with db.transaction() as connection:
        cursor = connection.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'followup', ?)
            """,
            (task_id, content),
        )
    return int(cursor.lastrowid)


def _add_operation(content: str) -> dict:
    return {
        "action": "add",
        "category": "preference",
        "content": content,
    }


def _apply(task_id: int, source_message_id: int, operation: dict):
    return db.apply_research_task_memory_operations(
        task_id,
        source_message_id,
        [operation],
    )


def _run_two(operation_a, operation_b):
    barrier = threading.Barrier(2)

    def invoke(operation):
        barrier.wait()
        try:
            return ("result", operation())
        except ValueError as exc:
            return ("value_error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(invoke, operation_a)
        future_b = executor.submit(invoke, operation_b)
        return future_a.result(), future_b.result()


@isolated_mysql_task_memory_test
def test_mysql_concurrent_equivalent_add_has_one_active_memory() -> None:
    task_id = _create_task()
    source_a = _create_source_message(task_id, "Remember Chinese")
    source_b = _create_source_message(task_id, "Also remember Chinese")
    content = "后续回答使用中文。"

    results = _run_two(
        lambda: _apply(task_id, source_a, _add_operation(content)),
        lambda: _apply(task_id, source_b, _add_operation(content)),
    )
    actions = [result[1][0]["action"] for result in results]
    memories = db.list_research_task_memories(task_id)

    assert [result[0] for result in results] == ["result", "result"]
    assert sorted(actions) == ["add", "noop_duplicate"]
    assert len(memories) == 1
    assert memories[0]["content"] == content
    task = db.get_research_task(task_id)
    assert task["task_memory_error"] is None
    assert task["task_memory_updated_at"] is not None


@isolated_mysql_task_memory_test
def test_mysql_add_vs_update_matches_one_legal_serial_order() -> None:
    task_id = _create_task()
    seed_source = _create_source_message(task_id, "Initial preference")
    existing_id = _apply(
        task_id,
        seed_source,
        _add_operation("后续回答使用英文。"),
    )[0]["memory_id"]
    add_source = _create_source_message(task_id, "Add Chinese")
    update_source = _create_source_message(task_id, "Change to Chinese")
    target_content = "后续回答使用中文。"

    results = _run_two(
        lambda: _apply(task_id, add_source, _add_operation(target_content)),
        lambda: _apply(
            task_id,
            update_source,
            {
                "action": "update",
                "memory_id": existing_id,
                "category": "preference",
                "content": target_content,
            },
        ),
    )
    actions = {result[1][0]["action"] for result in results}
    active = db.list_research_task_memories(task_id)

    assert [result[0] for result in results] == ["result", "result"]
    assert actions in (
        {"add", "merge_duplicate"},
        {"update", "noop_duplicate"},
    )
    assert len(active) == 1
    assert active[0]["content"] == target_content


@isolated_mysql_task_memory_test
def test_mysql_update_vs_remove_never_reactivates_removed_memory() -> None:
    task_id = _create_task()
    seed_source = _create_source_message(task_id, "Initial preference")
    memory_id = _apply(
        task_id,
        seed_source,
        _add_operation("后续回答使用英文。"),
    )[0]["memory_id"]
    update_source = _create_source_message(task_id, "Update preference")
    remove_source = _create_source_message(task_id, "Remove preference")

    results = _run_two(
        lambda: _apply(
            task_id,
            update_source,
            {
                "action": "update",
                "memory_id": memory_id,
                "category": "preference",
                "content": "后续回答使用中文。",
            },
        ),
        lambda: _apply(
            task_id,
            remove_source,
            {"action": "remove", "memory_id": memory_id},
        ),
    )

    assert any(result[0] == "result" for result in results)
    memory = db.get_research_task_memory(memory_id)
    assert memory["status"] == "removed"
    assert db.list_research_task_memories(task_id) == []


@isolated_mysql_task_memory_test
def test_mysql_cross_task_update_is_rejected_under_parent_lock() -> None:
    task_a = _create_task()
    task_b = _create_task()
    source_a = _create_source_message(task_a, "Task A source")
    source_b = _create_source_message(task_b, "Task B source")
    memory_id = _apply(
        task_a,
        source_a,
        _add_operation("后续回答使用英文。"),
    )[0]["memory_id"]

    with pytest.raises(ValueError, match="不属于当前任务"):
        _apply(
            task_b,
            source_b,
            {
                "action": "update",
                "memory_id": memory_id,
                "category": "preference",
                "content": "后续回答使用中文。",
            },
        )

    assert db.get_research_task_memory(memory_id)["status"] == "active"
    assert db.list_research_task_memories(task_b) == []


@isolated_mysql_task_memory_test
def test_mysql_parent_lock_serializes_same_task_not_different_task() -> None:
    task_a = _create_task()
    task_b = _create_task()
    source_a = _create_source_message(task_a, "Task A source")
    source_b = _create_source_message(task_b, "Task B source")
    locker = db.connect()
    locker.begin()
    locker.execute(
        "SELECT id FROM research_tasks WHERE id = ? FOR UPDATE",
        (task_a,),
    ).fetchone()

    started_a = threading.Event()
    started_b = threading.Event()

    def mutate_a():
        started_a.set()
        return _apply(task_a, source_a, _add_operation("Task A preference"))

    def mutate_b():
        started_b.set()
        return _apply(task_b, source_b, _add_operation("Task B preference"))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(mutate_a)
            future_b = executor.submit(mutate_b)
            assert started_a.wait(timeout=2)
            assert started_b.wait(timeout=2)
            started = time.monotonic()
            result_b = future_b.result(timeout=2)
            elapsed_b = time.monotonic() - started
            assert result_b[0]["action"] == "add"
            assert elapsed_b < 2
            assert future_a.done() is False
            locker.commit()
            result_a = future_a.result(timeout=2)
            assert result_a[0]["action"] == "add"
    finally:
        try:
            locker.rollback()
        finally:
            locker.close()

    assert len(db.list_research_task_memories(task_a)) == 1
    assert len(db.list_research_task_memories(task_b)) == 1


@isolated_mysql_task_memory_test
def test_mysql_memory_and_task_metadata_roll_back_together(monkeypatch) -> None:
    task_id = _create_task()
    source_id = _create_source_message(task_id, "Rollback source")
    assert db.record_research_task_memory_error(task_id, "old memory error")
    before = db.get_research_task(task_id)
    original_execute = db.MySQLConnectionAdapter.execute

    def fail_after_metadata_update(connection, sql, params=None):
        cursor = original_execute(connection, sql, params)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith(
            "UPDATE RESEARCH_TASKS SET TASK_MEMORY_ERROR = NULL"
        ):
            raise RuntimeError("simulated failure before commit")
        return cursor

    monkeypatch.setattr(
        db.MySQLConnectionAdapter,
        "execute",
        fail_after_metadata_update,
    )
    with pytest.raises(RuntimeError, match="before commit"):
        _apply(
            task_id,
            source_id,
            _add_operation("This must roll back"),
        )

    after = db.get_research_task(task_id)
    assert db.list_research_task_memories(task_id) == []
    assert after["task_memory_error"] == "old memory error"
    assert after["task_memory_updated_at"] == before["task_memory_updated_at"]

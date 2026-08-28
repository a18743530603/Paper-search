import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


def _job_time(value: datetime):
    return db._db_timestamp(value)


def _optional_job_time(value):
    return _job_time(value) if value is not None else None


def _replace_jobs(entries: list[dict]) -> list[int]:
    now = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
    ids: list[int] = []
    with db.transaction() as connection:
        connection.execute("DELETE FROM background_jobs")
        for index, entry in enumerate(entries):
            available_at = entry.get("available_at", now)
            cursor = connection.execute(
                """
                INSERT INTO background_jobs (
                    job_type, payload_json, dedupe_key, status, priority,
                    attempt_count, max_attempts, available_at,
                    lease_expires_at, worker_id, last_error,
                    created_at, updated_at, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("job_type", "claim_test"),
                    entry.get("payload_json", "{}"),
                    f"claim-test-{index}-{uuid4().hex}",
                    entry.get("status", "queued"),
                    entry.get("priority", 0),
                    entry.get("attempt_count", 0),
                    entry.get("max_attempts", 3),
                    _job_time(available_at),
                    _optional_job_time(entry.get("lease_expires_at")),
                    entry.get("worker_id"),
                    entry.get("last_error"),
                    _job_time(now),
                    _job_time(entry.get("updated_at", now)),
                    _optional_job_time(entry.get("started_at")),
                    _optional_job_time(entry.get("completed_at")),
                ),
            )
            ids.append(int(cursor.lastrowid))
    return ids


@contextmanager
def _mysql_claim_database():
    database_name = f"paper_hunter_phase4a_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_phase4a_[0-9a-f]{32}", database_name)
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
        yield database_name
    finally:
        db.BUSINESS_DB_BACKEND = original_backend
        db.BUSINESS_DB_NAME = original_name
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin.close()


def isolated_mysql_claim_test(function):
    @pytest.mark.skipif(
        not MYSQL_INTEGRATION_ENABLED,
        reason="set PAPER_HUNTER_MYSQL_TEST=1 to run MySQL claim tests",
    )
    @wraps(function)
    def run_in_isolated_database(*args, **kwargs):
        with _mysql_claim_database():
            return function(*args, **kwargs)

    return run_in_isolated_database


@isolated_mysql_claim_test
def test_mysql_two_workers_claim_one_job_once() -> None:
    now = datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc)
    job_id = _replace_jobs([{"available_at": now - timedelta(seconds=1)}])[0]
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return db.claim_next_background_job(worker_id, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == job_id
    assert claimed[0]["worker_id"] in {"worker-a", "worker-b"}
    assert claimed[0]["attempt_count"] == 1
    assert datetime.fromisoformat(claimed[0]["lease_expires_at"]) == (
        now + timedelta(seconds=60)
    )
    assert datetime.fromisoformat(claimed[0]["started_at"]) == now
    assert sum(result is None for result in results) == 1


@isolated_mysql_claim_test
def test_mysql_eight_workers_claim_100_jobs_without_duplicates() -> None:
    now = datetime(2026, 8, 27, 0, 2, tzinfo=timezone.utc)
    job_ids = _replace_jobs(
        [
            {"available_at": now - timedelta(seconds=1), "priority": index % 5}
            for index in range(100)
        ]
    )
    barrier = threading.Barrier(8)

    def drain(worker_index: int):
        worker_id = f"worker-{worker_index}"
        claimed: list[tuple[int, str]] = []
        barrier.wait()
        while True:
            job = db.claim_next_background_job(worker_id, now=now)
            if job is None:
                return claimed
            claimed.append((int(job["id"]), str(job["worker_id"])))

    with ThreadPoolExecutor(max_workers=8) as executor:
        worker_results = list(executor.map(drain, range(8)))

    claims = [claim for worker_claims in worker_results for claim in worker_claims]
    claimed_ids = [job_id for job_id, _ in claims]
    assert len(claimed_ids) == 100
    assert len(set(claimed_ids)) == 100
    assert set(claimed_ids) == set(job_ids)
    assert all(owner.startswith("worker-") for _, owner in claims)

    with db.read_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, status, worker_id, attempt_count
            FROM background_jobs
            ORDER BY id
            """
        ).fetchall()
    assert all(row["status"] == "running" for row in rows)
    assert all(row["worker_id"] for row in rows)
    assert all(row["attempt_count"] == 1 for row in rows)


@isolated_mysql_claim_test
def test_mysql_claim_skips_locked_highest_priority_job() -> None:
    now = datetime(2026, 8, 27, 0, 3, tzinfo=timezone.utc)
    high_id, next_id = _replace_jobs(
        [
            {"available_at": now - timedelta(seconds=1), "priority": 100},
            {"available_at": now - timedelta(seconds=1), "priority": 50},
        ]
    )
    locking_connection = db.connect()
    try:
        locking_connection.begin()
        locked = locking_connection.execute(
            """
            SELECT id FROM background_jobs
            WHERE id = ?
            FOR UPDATE
            """,
            (high_id,),
        ).fetchone()
        assert locked["id"] == high_id

        started = time.monotonic()
        claimed = db.claim_next_background_job("worker-skip", now=now)
        elapsed = time.monotonic() - started

        assert claimed["id"] == next_id
        assert elapsed < 2.0
    finally:
        locking_connection.rollback()
        locking_connection.close()


@isolated_mysql_claim_test
def test_mysql_claim_respects_available_at() -> None:
    now = datetime(2026, 8, 27, 0, 4, tzinfo=timezone.utc)
    due_id, future_id = _replace_jobs(
        [
            {"available_at": now - timedelta(microseconds=1)},
            {"available_at": now + timedelta(seconds=30), "priority": 100},
        ]
    )

    claimed = db.claim_next_background_job("worker-due", now=now)
    empty = db.claim_next_background_job("worker-empty", now=now)

    assert claimed["id"] == due_id
    assert empty is None
    assert db.get_background_job(future_id)["status"] == "queued"


@isolated_mysql_claim_test
def test_mysql_claim_preserves_priority_then_id_order() -> None:
    now = datetime(2026, 8, 27, 0, 5, tzinfo=timezone.utc)
    ids = _replace_jobs(
        [
            {"available_at": now, "priority": 1},
            {"available_at": now, "priority": 10},
            {"available_at": now, "priority": 10},
            {"available_at": now, "priority": 5},
        ]
    )

    claimed_ids = [
        db.claim_next_background_job(f"worker-order-{index}", now=now)["id"]
        for index in range(4)
    ]

    assert claimed_ids == [ids[1], ids[2], ids[3], ids[0]]


@isolated_mysql_claim_test
def test_mysql_claim_preserves_status_and_job_type_filters() -> None:
    now = datetime(2026, 8, 27, 0, 5, 30, tzinfo=timezone.utc)
    queued_id, retry_id, other_id, failed_id = _replace_jobs(
        [
            {"available_at": now, "job_type": "allowed", "priority": 1},
            {
                "available_at": now,
                "job_type": "allowed",
                "status": "retry_wait",
                "attempt_count": 1,
                "priority": 10,
            },
            {"available_at": now, "job_type": "other", "priority": 100},
            {
                "available_at": now,
                "job_type": "allowed",
                "status": "failed",
                "priority": 200,
            },
        ]
    )

    first = db.claim_next_background_job(
        "worker-filter-1",
        job_types=("allowed", "allowed"),
        now=now,
    )
    second = db.claim_next_background_job(
        "worker-filter-2",
        job_types=("allowed",),
        now=now,
    )
    empty = db.claim_next_background_job(
        "worker-filter-3",
        job_types=("allowed",),
        now=now,
    )

    assert first["id"] == retry_id
    assert first["attempt_count"] == 2
    assert second["id"] == queued_id
    assert empty is None
    assert db.get_background_job(other_id)["status"] == "queued"
    assert db.get_background_job(failed_id)["status"] == "failed"


@isolated_mysql_claim_test
def test_mysql_claim_rolls_back_update_on_exception(monkeypatch) -> None:
    now = datetime(2026, 8, 27, 0, 6, tzinfo=timezone.utc)
    job_id = _replace_jobs([{"available_at": now}])[0]
    original_execute = db.MySQLConnectionAdapter.execute

    def execute_then_fail(connection, sql, params=None):
        cursor = original_execute(connection, sql, params)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("UPDATE BACKGROUND_JOBS") and (
            "ATTEMPT_COUNT = ATTEMPT_COUNT + 1" in normalized
        ):
            raise RuntimeError("simulated claim failure before commit")
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(db.MySQLConnectionAdapter, "execute", execute_then_fail)
        with pytest.raises(RuntimeError, match="before commit"):
            db.claim_next_background_job("worker-failing", now=now)

    rolled_back = db.get_background_job(job_id)
    assert rolled_back["status"] == "queued"
    assert rolled_back["worker_id"] is None
    assert rolled_back["lease_expires_at"] is None
    assert rolled_back["started_at"] is None
    assert rolled_back["attempt_count"] == 0

    claimed = db.claim_next_background_job("worker-after-rollback", now=now)
    assert claimed["id"] == job_id
    assert claimed["worker_id"] == "worker-after-rollback"


@isolated_mysql_claim_test
def test_mysql_correct_owner_renews_valid_lease() -> None:
    now = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=10),
                "available_at": now - timedelta(seconds=1),
                "attempt_count": 1,
                "last_error": "preserve me",
            }
        ]
    )[0]

    renewed = db.renew_background_job_lease(
        job_id,
        "worker-a",
        lease_seconds=30,
        now=now,
    )
    job = db.get_background_job(job_id)

    assert renewed is True
    assert datetime.fromisoformat(job["lease_expires_at"]) == (
        now + timedelta(seconds=30)
    )
    assert job["worker_id"] == "worker-a"
    assert job["status"] == "running"
    assert job["attempt_count"] == 1
    assert job["last_error"] == "preserve me"


@isolated_mysql_claim_test
def test_mysql_wrong_owner_cannot_renew_lease() -> None:
    now = datetime(2026, 8, 27, 1, 1, tzinfo=timezone.utc)
    original_expiry = now + timedelta(seconds=20)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": original_expiry,
                "available_at": now,
            }
        ]
    )[0]

    renewed = db.renew_background_job_lease(
        job_id,
        "worker-b",
        lease_seconds=60,
        now=now,
    )

    assert renewed is False
    assert datetime.fromisoformat(
        db.get_background_job(job_id)["lease_expires_at"]
    ) == original_expiry


@isolated_mysql_claim_test
def test_mysql_completed_job_cannot_renew() -> None:
    now = datetime(2026, 8, 27, 1, 2, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "succeeded",
                "worker_id": "worker-a",
                "lease_expires_at": None,
                "available_at": now,
                "completed_at": now,
            }
        ]
    )[0]

    assert db.renew_background_job_lease(
        job_id,
        "worker-a",
        now=now,
    ) is False
    assert db.get_background_job(job_id)["lease_expires_at"] is None


@isolated_mysql_claim_test
def test_mysql_expired_lease_cannot_be_revived() -> None:
    now = datetime(2026, 8, 27, 1, 3, tzinfo=timezone.utc)
    expired_at = now - timedelta(microseconds=1)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": expired_at,
                "available_at": now,
            }
        ]
    )[0]

    assert db.renew_background_job_lease(
        job_id,
        "worker-a",
        lease_seconds=60,
        now=now,
    ) is False
    assert datetime.fromisoformat(
        db.get_background_job(job_id)["lease_expires_at"]
    ) == expired_at


@isolated_mysql_claim_test
def test_mysql_concurrent_renew_keeps_lease_monotonic() -> None:
    base = datetime(2026, 8, 27, 1, 4, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": base + timedelta(seconds=30),
                "available_at": base,
            }
        ]
    )[0]
    barrier = threading.Barrier(2)

    def renew(current: datetime):
        barrier.wait()
        return db.renew_background_job_lease(
            job_id,
            "worker-a",
            lease_seconds=100,
            now=current,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                renew,
                (base + timedelta(seconds=10), base + timedelta(seconds=20)),
            )
        )

    job = db.get_background_job(job_id)
    assert results == [True, True]
    assert datetime.fromisoformat(job["lease_expires_at"]) == (
        base + timedelta(seconds=120)
    )
    assert job["status"] == "running"
    assert job["worker_id"] == "worker-a"


@isolated_mysql_claim_test
def test_mysql_correct_owner_completes_with_valid_lease() -> None:
    now = datetime(2026, 8, 27, 1, 5, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=10),
                "available_at": now,
                "attempt_count": 1,
                "last_error": "old error",
            }
        ]
    )[0]

    completed = db.complete_background_job(job_id, "worker-a", now=now)
    job = db.get_background_job(job_id)

    assert completed is True
    assert job["status"] == "succeeded"
    assert job["worker_id"] == "worker-a"
    assert job["lease_expires_at"] is None
    assert job["last_error"] is None
    assert job["attempt_count"] == 1
    assert datetime.fromisoformat(job["completed_at"]) == now
    assert datetime.fromisoformat(job["updated_at"]) == now


@isolated_mysql_claim_test
def test_mysql_wrong_owner_cannot_complete() -> None:
    now = datetime(2026, 8, 27, 1, 6, tzinfo=timezone.utc)
    expiry = now + timedelta(seconds=10)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": expiry,
                "available_at": now,
            }
        ]
    )[0]

    assert db.complete_background_job(job_id, "worker-b", now=now) is False
    job = db.get_background_job(job_id)
    assert job["status"] == "running"
    assert job["worker_id"] == "worker-a"
    assert datetime.fromisoformat(job["lease_expires_at"]) == expiry
    assert job["completed_at"] is None


@isolated_mysql_claim_test
def test_mysql_repeat_complete_fails_without_rewriting_timestamp() -> None:
    now = datetime(2026, 8, 27, 1, 7, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=10),
                "available_at": now,
            }
        ]
    )[0]

    assert db.complete_background_job(job_id, "worker-a", now=now) is True
    first_completed_at = db.get_background_job(job_id)["completed_at"]
    assert db.complete_background_job(
        job_id,
        "worker-a",
        now=now + timedelta(seconds=5),
    ) is False
    assert db.get_background_job(job_id)["completed_at"] == first_completed_at


@isolated_mysql_claim_test
def test_mysql_stale_worker_cannot_complete() -> None:
    now = datetime(2026, 8, 27, 1, 8, tzinfo=timezone.utc)
    changed_owner_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-b",
                "lease_expires_at": now + timedelta(seconds=10),
                "available_at": now,
            }
        ]
    )[0]
    assert db.complete_background_job(
        changed_owner_id,
        "worker-a",
        now=now,
    ) is False

    expired_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(microseconds=1),
                "available_at": now,
            }
        ]
    )[0]
    assert db.complete_background_job(expired_id, "worker-a", now=now) is False
    assert db.get_background_job(expired_id)["status"] == "running"


@isolated_mysql_claim_test
def test_mysql_renew_vs_complete_race_finishes_consistently() -> None:
    now = datetime(2026, 8, 27, 1, 9, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=10),
                "available_at": now,
            }
        ]
    )[0]
    barrier = threading.Barrier(2)

    def renew():
        barrier.wait()
        return db.renew_background_job_lease(
            job_id,
            "worker-a",
            lease_seconds=60,
            now=now,
        )

    def complete():
        barrier.wait()
        return db.complete_background_job(job_id, "worker-a", now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        renew_future = executor.submit(renew)
        complete_future = executor.submit(complete)
        renew_result = renew_future.result()
        complete_result = complete_future.result()

    job = db.get_background_job(job_id)
    assert complete_result is True
    assert renew_result in {True, False}
    assert job["status"] == "succeeded"
    assert job["lease_expires_at"] is None
    assert job["completed_at"] is not None
    assert db.renew_background_job_lease(
        job_id,
        "worker-a",
        now=now + timedelta(seconds=1),
    ) is False


@isolated_mysql_claim_test
def test_mysql_valid_owner_failure_enters_retry_wait() -> None:
    now = datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=30),
                "available_at": now,
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]

    status = db.fail_background_job(
        job_id,
        "worker-a",
        "temporary failure",
        retry_delay_seconds=5,
        now=now,
    )
    job = db.get_background_job(job_id)

    assert status == "retry_wait"
    assert job["status"] == "retry_wait"
    assert job["worker_id"] is None
    assert job["lease_expires_at"] is None
    assert job["last_error"] == "temporary failure"
    assert job["completed_at"] is None


@isolated_mysql_claim_test
def test_mysql_failure_at_max_attempts_enters_failed() -> None:
    now = datetime(2026, 8, 27, 2, 1, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=30),
                "available_at": now,
                "attempt_count": 3,
                "max_attempts": 3,
            }
        ]
    )[0]

    status = db.fail_background_job(
        job_id,
        "worker-a",
        "permanent failure",
        retry_delay_seconds=5,
        now=now,
    )
    job = db.get_background_job(job_id)

    assert status == "failed"
    assert job["status"] == "failed"
    assert job["worker_id"] is None
    assert job["lease_expires_at"] is None
    assert job["last_error"] == "permanent failure"
    assert datetime.fromisoformat(job["completed_at"]) == now


@isolated_mysql_claim_test
def test_mysql_wrong_worker_failure_is_rejected() -> None:
    now = datetime(2026, 8, 27, 2, 2, tzinfo=timezone.utc)
    expiry = now + timedelta(seconds=30)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-b",
                "lease_expires_at": expiry,
                "available_at": now,
                "attempt_count": 1,
                "last_error": "original error",
            }
        ]
    )[0]

    status = db.fail_background_job(
        job_id,
        "worker-a",
        "stale worker error",
        now=now,
    )
    job = db.get_background_job(job_id)

    assert status is None
    assert job["status"] == "running"
    assert job["worker_id"] == "worker-b"
    assert datetime.fromisoformat(job["lease_expires_at"]) == expiry
    assert job["last_error"] == "original error"


@isolated_mysql_claim_test
def test_mysql_expired_owner_failure_is_rejected() -> None:
    now = datetime(2026, 8, 27, 2, 3, tzinfo=timezone.utc)
    expired_at = now - timedelta(microseconds=1)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": expired_at,
                "available_at": now,
                "attempt_count": 1,
                "last_error": "original error",
            }
        ]
    )[0]

    status = db.fail_background_job(
        job_id,
        "worker-a",
        "late failure",
        now=now,
    )
    job = db.get_background_job(job_id)

    assert status is None
    assert job["status"] == "running"
    assert job["worker_id"] == "worker-a"
    assert datetime.fromisoformat(job["lease_expires_at"]) == expired_at
    assert job["last_error"] == "original error"


@isolated_mysql_claim_test
def test_mysql_failure_does_not_increment_attempt_count_again() -> None:
    now = datetime(2026, 8, 27, 2, 4, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=30),
                "available_at": now,
                "attempt_count": 2,
                "max_attempts": 4,
            }
        ]
    )[0]

    assert db.fail_background_job(
        job_id,
        "worker-a",
        "attempt failed",
        now=now,
    ) == "retry_wait"
    assert db.get_background_job(job_id)["attempt_count"] == 2


@isolated_mysql_claim_test
def test_mysql_failure_preserves_retry_backoff_timestamp() -> None:
    now = datetime(2026, 8, 27, 2, 5, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=30),
                "available_at": now,
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]

    assert db.fail_background_job(
        job_id,
        "worker-a",
        "retry later",
        retry_delay_seconds=37,
        now=now,
    ) == "retry_wait"
    assert datetime.fromisoformat(
        db.get_background_job(job_id)["available_at"]
    ) == now + timedelta(seconds=37)


@isolated_mysql_claim_test
def test_mysql_recovery_requeues_expired_running_job() -> None:
    now = datetime(2026, 8, 27, 2, 6, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(microseconds=1),
                "available_at": now - timedelta(seconds=30),
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]

    recovered = db.recover_expired_background_jobs(now=now)
    job = db.get_background_job(job_id)

    assert [item["id"] for item in recovered] == [job_id]
    assert recovered[0]["status"] == "queued"
    assert job["status"] == "queued"
    assert job["worker_id"] is None
    assert job["lease_expires_at"] is None
    assert datetime.fromisoformat(job["available_at"]) == now
    assert job["last_error"] == (
        "Worker lease expired before the task completed"
    )
    assert job["completed_at"] is None


@isolated_mysql_claim_test
def test_mysql_recovery_fails_expired_job_at_max_attempts() -> None:
    now = datetime(2026, 8, 27, 2, 6, 30, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(microseconds=1),
                "available_at": now - timedelta(seconds=30),
                "attempt_count": 3,
                "max_attempts": 3,
            }
        ]
    )[0]

    recovered = db.recover_expired_background_jobs(now=now)
    job = db.get_background_job(job_id)

    assert [item["id"] for item in recovered] == [job_id]
    assert recovered[0]["status"] == "failed"
    assert job["status"] == "failed"
    assert job["attempt_count"] == 3
    assert job["worker_id"] is None
    assert job["lease_expires_at"] is None
    assert datetime.fromisoformat(job["completed_at"]) == now


@isolated_mysql_claim_test
def test_mysql_recovery_ignores_nonexpired_running_job() -> None:
    now = datetime(2026, 8, 27, 2, 7, tzinfo=timezone.utc)
    expiry = now + timedelta(microseconds=1)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": expiry,
                "available_at": now,
                "attempt_count": 1,
            }
        ]
    )[0]

    assert db.recover_expired_background_jobs(now=now) == []
    job = db.get_background_job(job_id)
    assert job["status"] == "running"
    assert job["worker_id"] == "worker-a"
    assert datetime.fromisoformat(job["lease_expires_at"]) == expiry


@isolated_mysql_claim_test
def test_mysql_recovery_ignores_succeeded_job() -> None:
    now = datetime(2026, 8, 27, 2, 8, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "succeeded",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(seconds=1),
                "available_at": now,
                "completed_at": now - timedelta(seconds=2),
            }
        ]
    )[0]

    assert db.recover_expired_background_jobs(now=now) == []
    assert db.get_background_job(job_id)["status"] == "succeeded"


@isolated_mysql_claim_test
def test_mysql_recovery_ignores_failed_job() -> None:
    now = datetime(2026, 8, 27, 2, 9, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "failed",
                "worker_id": None,
                "lease_expires_at": now - timedelta(seconds=1),
                "available_at": now,
                "completed_at": now - timedelta(seconds=2),
                "last_error": "terminal failure",
            }
        ]
    )[0]

    assert db.recover_expired_background_jobs(now=now) == []
    job = db.get_background_job(job_id)
    assert job["status"] == "failed"
    assert job["last_error"] == "terminal failure"


@isolated_mysql_claim_test
def test_mysql_heartbeat_vs_recovery_has_one_legal_winner() -> None:
    boundary = datetime(2026, 8, 27, 2, 10, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": boundary,
                "available_at": boundary - timedelta(seconds=30),
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]
    barrier = threading.Barrier(2)

    def renew():
        barrier.wait()
        return db.renew_background_job_lease(
            job_id,
            "worker-a",
            lease_seconds=60,
            now=boundary - timedelta(microseconds=1),
        )

    def recover():
        barrier.wait()
        return db.recover_expired_background_jobs(now=boundary)

    with ThreadPoolExecutor(max_workers=2) as executor:
        renew_future = executor.submit(renew)
        recover_future = executor.submit(recover)
        renewed = renew_future.result()
        recovered = recover_future.result()

    job = db.get_background_job(job_id)
    assert renewed is (recovered == [])
    if renewed:
        assert job["status"] == "running"
        assert job["worker_id"] == "worker-a"
        assert datetime.fromisoformat(job["lease_expires_at"]) > boundary
    else:
        assert [item["id"] for item in recovered] == [job_id]
        assert job["status"] == "queued"
        assert job["worker_id"] is None
        assert job["lease_expires_at"] is None


@isolated_mysql_claim_test
def test_mysql_complete_vs_recovery_has_one_legal_winner() -> None:
    boundary = datetime(2026, 8, 27, 2, 11, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": boundary,
                "available_at": boundary - timedelta(seconds=30),
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]
    barrier = threading.Barrier(2)

    def complete():
        barrier.wait()
        return db.complete_background_job(
            job_id,
            "worker-a",
            now=boundary - timedelta(microseconds=1),
        )

    def recover():
        barrier.wait()
        return db.recover_expired_background_jobs(now=boundary)

    with ThreadPoolExecutor(max_workers=2) as executor:
        complete_future = executor.submit(complete)
        recover_future = executor.submit(recover)
        completed = complete_future.result()
        recovered = recover_future.result()

    job = db.get_background_job(job_id)
    assert completed is (recovered == [])
    if completed:
        assert job["status"] == "succeeded"
        assert job["lease_expires_at"] is None
    else:
        assert [item["id"] for item in recovered] == [job_id]
        assert job["status"] == "queued"
        assert job["worker_id"] is None


@isolated_mysql_claim_test
def test_mysql_fail_vs_recovery_has_one_legal_winner() -> None:
    boundary = datetime(2026, 8, 27, 2, 12, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": boundary,
                "available_at": boundary - timedelta(seconds=30),
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]
    barrier = threading.Barrier(2)

    def fail():
        barrier.wait()
        return db.fail_background_job(
            job_id,
            "worker-a",
            "worker failure",
            retry_delay_seconds=9,
            now=boundary - timedelta(microseconds=1),
        )

    def recover():
        barrier.wait()
        return db.recover_expired_background_jobs(now=boundary)

    with ThreadPoolExecutor(max_workers=2) as executor:
        fail_future = executor.submit(fail)
        recover_future = executor.submit(recover)
        failed_status = fail_future.result()
        recovered = recover_future.result()

    job = db.get_background_job(job_id)
    assert (failed_status == "retry_wait") is (recovered == [])
    assert job["attempt_count"] == 1
    if failed_status == "retry_wait":
        assert job["status"] == "retry_wait"
        assert job["last_error"] == "worker failure"
    else:
        assert failed_status is None
        assert [item["id"] for item in recovered] == [job_id]
        assert job["status"] == "queued"
        assert job["last_error"] == (
            "Worker lease expired before the task completed"
        )
    assert job["worker_id"] is None
    assert job["lease_expires_at"] is None


@isolated_mysql_claim_test
def test_mysql_two_recovery_executors_do_not_recover_job_twice() -> None:
    now = datetime(2026, 8, 27, 2, 13, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(microseconds=1),
                "available_at": now - timedelta(seconds=30),
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]
    barrier = threading.Barrier(2)

    def recover():
        barrier.wait()
        return db.recover_expired_background_jobs(now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: recover(), range(2)))

    recovered_ids = [item["id"] for result in results for item in result]
    assert recovered_ids == [job_id]
    job = db.get_background_job(job_id)
    assert job["status"] == "queued"
    assert job["attempt_count"] == 1
    assert job["worker_id"] is None
    assert job["lease_expires_at"] is None


def test_sqlite_renew_and_complete_require_live_owner(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "ownership.db")
    db.init_db()
    now = datetime(2026, 8, 27, 1, 10, tzinfo=timezone.utc)
    job_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=10),
                "available_at": now,
            }
        ]
    )[0]

    assert db.renew_background_job_lease(
        job_id,
        "worker-b",
        now=now,
    ) is False
    assert db.renew_background_job_lease(
        job_id,
        "worker-a",
        lease_seconds=20,
        now=now,
    ) is True
    assert db.complete_background_job(job_id, "worker-b", now=now) is False
    assert db.complete_background_job(job_id, "worker-a", now=now) is True
    assert db.renew_background_job_lease(
        job_id,
        "worker-a",
        now=now,
    ) is False

    expired_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(microseconds=1),
                "available_at": now,
            }
        ]
    )[0]
    assert db.renew_background_job_lease(
        expired_id,
        "worker-a",
        now=now,
    ) is False
    assert db.complete_background_job(expired_id, "worker-a", now=now) is False


def test_sqlite_fail_and_recovery_require_current_lease_state(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "fail-recovery.db")
    db.init_db()
    now = datetime(2026, 8, 27, 2, 14, tzinfo=timezone.utc)
    valid_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now + timedelta(seconds=30),
                "available_at": now,
                "attempt_count": 1,
                "max_attempts": 3,
            }
        ]
    )[0]

    assert db.fail_background_job(
        valid_id,
        "worker-a",
        "temporary failure",
        retry_delay_seconds=11,
        now=now,
    ) == "retry_wait"
    valid_job = db.get_background_job(valid_id)
    assert valid_job["attempt_count"] == 1
    assert datetime.fromisoformat(valid_job["available_at"]) == (
        now + timedelta(seconds=11)
    )

    expired_id = _replace_jobs(
        [
            {
                "status": "running",
                "worker_id": "worker-a",
                "lease_expires_at": now - timedelta(microseconds=1),
                "available_at": now,
                "attempt_count": 1,
                "last_error": "original error",
            }
        ]
    )[0]
    assert db.fail_background_job(
        expired_id,
        "worker-a",
        "late error",
        now=now,
    ) is None
    recovered = db.recover_expired_background_jobs(now=now)
    expired_job = db.get_background_job(expired_id)
    assert [item["id"] for item in recovered] == [expired_id]
    assert expired_job["status"] == "queued"
    assert expired_job["attempt_count"] == 1
    assert expired_job["worker_id"] is None
    assert expired_job["lease_expires_at"] is None


def test_sqlite_claim_keeps_begin_immediate_and_single_owner(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "claim.db")
    db.init_db()
    now = datetime(2026, 8, 27, 0, 7, tzinfo=timezone.utc)
    job_id = _replace_jobs([{"available_at": now}])[0]
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return db.claim_next_background_job(worker_id, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("sqlite-a", "sqlite-b")))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == job_id
    assert claimed[0]["attempt_count"] == 1
    assert db.get_background_job(job_id)["worker_id"] in {"sqlite-a", "sqlite-b"}

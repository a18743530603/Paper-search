import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db
from my_agent_project.mysql_data_migration import (
    BUSINESS_TABLES,
    MigrationDataError,
    MigrationError,
    TargetColumn,
    _convert_value,
    convert_datetime,
    file_snapshot,
    guard_target_database,
    migrate_sqlite_business_data_to_mysql,
    open_sqlite_source_read_only,
)
from my_agent_project.mysql_migrations import apply_mysql_migrations


MYSQL_MIGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_MIGRATION_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


def _column(
    name: str,
    data_type: str,
    *,
    column_type: str | None = None,
    nullable: bool = False,
    max_length: int | None = None,
) -> TargetColumn:
    return TargetColumn(
        name=name,
        data_type=data_type,
        column_type=column_type or data_type,
        nullable=nullable,
        default=None,
        extra="",
        ordinal=1,
        max_length=max_length,
    )


def test_migration_refuses_formal_and_unsafe_target_names() -> None:
    for name in ("paper_hunter", "PAPER_HUNTER", "", "db-name", "db;drop"):
        with pytest.raises(MigrationError):
            guard_target_database(name)
    guard_target_database("paper_hunter_migration_test_0123abcd")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-24T16:09:37.771+08:00", datetime(2026, 8, 24, 8, 9, 37, 771000)),
        ("2026-08-24T08:09:37Z", datetime(2026, 8, 24, 8, 9, 37)),
        ("2026-08-24 08:09:37", datetime(2026, 8, 24, 8, 9, 37)),
        ("2026-08-24 08:09:37.123456", datetime(2026, 8, 24, 8, 9, 37, 123456)),
        (None, None),
    ],
)
def test_datetime_conversion_normalizes_to_utc(value, expected) -> None:
    assert convert_datetime(value, table="events", row_id="1", column="created_at") == expected


def test_datetime_conversion_reports_exact_location() -> None:
    with pytest.raises(MigrationDataError) as exc_info:
        convert_datetime("not-a-date", table="events", row_id="42", column="created_at")
    message = str(exc_info.value)
    assert "table=events" in message
    assert "row=42" in message
    assert "column=created_at" in message


@pytest.mark.parametrize(
    ("value", "column", "category"),
    [
        (-1, _column("count", "bigint", column_type="bigint unsigned"), "unsigned"),
        ("", _column("count", "int"), "integer"),
        ("None", _column("count", "int"), "integer"),
        (float("nan"), _column("score", "double"), "numeric"),
        (float("inf"), _column("score", "double"), "numeric"),
        (3, _column("payload", "longtext"), "text"),
        ("abcdef", _column("status", "varchar", max_length=5), "long"),
    ],
)
def test_strict_value_conversion_rejects_invalid_sqlite_dynamic_types(
    value,
    column,
    category,
) -> None:
    with pytest.raises(MigrationDataError) as exc_info:
        _convert_value(value, column, table="sample", row_id="[7]")
    assert category in str(exc_info.value).lower()


def test_sqlite_source_connection_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    setup = sqlite3.connect(source)
    setup.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    setup.execute("INSERT INTO sample (value) VALUES ('original')")
    setup.commit()
    setup.close()
    before = file_snapshot(source)
    connection = open_sqlite_source_read_only(source)
    try:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "original"
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE sample SET value = 'changed'")
    finally:
        connection.rollback()
        connection.close()
    assert file_snapshot(source) == before


@contextmanager
def _temporary_migration_database():
    database_name = f"paper_hunter_migration_test_{uuid4().hex}"
    guard_target_database(database_name)
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
    connection = None
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        created = True
        connection = pymysql.connect(
            host=config.BUSINESS_DB_HOST,
            port=config.BUSINESS_DB_PORT,
            user=config.BUSINESS_DB_USER,
            password=config.BUSINESS_DB_PASSWORD,
            database=database_name,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=config.BUSINESS_DB_CONNECT_TIMEOUT,
            read_timeout=config.BUSINESS_DB_READ_TIMEOUT,
            write_timeout=config.BUSINESS_DB_WRITE_TIMEOUT,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        assert apply_mysql_migrations(connection) == ["001", "002", "003"]
        yield database_name, connection
    finally:
        if connection is not None:
            connection.close()
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin.close()


def _source_samples() -> dict:
    connection = open_sqlite_source_read_only(config.DATABASE_PATH)
    try:
        pdf = dict(
            connection.execute(
                "SELECT source_hash, content FROM pdf_text_cache ORDER BY source_hash, page_number LIMIT 1"
            ).fetchone()
        )
        embedding = dict(
            connection.execute(
                "SELECT cache_namespace, content_hash, vector_json FROM embedding_cache ORDER BY cache_namespace, content_hash LIMIT 1"
            ).fetchone()
        )
        llm = dict(
            connection.execute(
                "SELECT cache_namespace, input_hash, response_json FROM llm_response_cache ORDER BY cache_namespace, input_hash LIMIT 1"
            ).fetchone()
        )
    finally:
        connection.rollback()
        connection.close()
    return {"pdf": pdf, "embedding": embedding, "llm": llm}


def _application_read_rehearsal(database_name: str, samples: dict) -> dict:
    original_backend = db.BUSINESS_DB_BACKEND
    original_database = db.BUSINESS_DB_NAME
    db.BUSINESS_DB_BACKEND = "mysql"
    db.BUSINESS_DB_NAME = database_name
    try:
        pdf_pages = db.get_cached_pdf_pages(samples["pdf"]["source_hash"])
        embeddings = db.get_cached_embeddings(
            samples["embedding"]["cache_namespace"],
            [samples["embedding"]["content_hash"]],
        )
        llm = db.get_cached_llm_response(
            samples["llm"]["cache_namespace"],
            samples["llm"]["input_hash"],
        )
        checks = {
            "papers": db.list_papers() == [],
            "tasks": db.list_research_tasks() == [],
            "report": db.get_research_report(1) is None,
            "messages": db.list_research_messages(1) == [],
            "memories": db.list_research_task_memories(1) == [],
            "jobs": db.list_background_jobs() == [],
            "document": db.get_document(1) is None,
            "chunks": db.list_paper_chunks(1) == [],
            "traces": len(db.list_agent_metric_traces()) == 420,
            "pdf_cache": bool(pdf_pages) and pdf_pages[0] == samples["pdf"]["content"],
            "embedding_cache": samples["embedding"]["content_hash"] in embeddings,
            "llm_cache": llm == json.loads(samples["llm"]["response_json"]),
            "evaluation": db.list_evaluation_runs() == [],
        }
        assert all(checks.values()), checks
        return checks
    finally:
        db.BUSINESS_DB_BACKEND = original_backend
        db.BUSINESS_DB_NAME = original_database


@pytest.mark.skipif(
    not MYSQL_MIGRATION_ENABLED,
    reason="set PAPER_HUNTER_MYSQL_MIGRATION_TEST=1 to run the real migration rehearsal",
)
def test_real_sqlite_to_temporary_mysql_migration_rehearsal() -> None:
    source_before = file_snapshot(config.DATABASE_PATH)
    with _temporary_migration_database() as (_, failure_target):
        with pytest.raises(MigrationError, match="Injected migration failure"):
            migrate_sqlite_business_data_to_mysql(
                config.DATABASE_PATH,
                failure_target,
                batch_size=500,
                fail_after_batch=("embedding_cache", 2),
            )
        with failure_target.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM embedding_cache")
            assert int(cursor.fetchone()["n"]) == 500
            cursor.execute("SELECT COUNT(*) AS n FROM llm_response_cache")
            assert int(cursor.fetchone()["n"]) == 0
    assert file_snapshot(config.DATABASE_PATH) == source_before

    samples = _source_samples()
    with _temporary_migration_database() as (database_name, target):
        report = migrate_sqlite_business_data_to_mysql(
            config.DATABASE_PATH,
            target,
            batch_size=500,
        )
        assert report.source_before == report.source_after == source_before
        assert [table.table for table in report.tables] == list(BUSINESS_TABLES)
        assert all(table.matches for table in report.tables)
        assert report.foreign_key_checks_enabled
        assert report.foreign_key_count == 19
        assert not any(report.foreign_key_orphans.values())
        assert not any(report.generated_column_errors.values())
        assert report.question_hash_errors == 0
        assert report.source_active_dedupe_conflicts == []
        assert report.source_question_duplicates == []
        assert all(
            values["next_id"] > values["max_id"]
            for values in report.auto_increment.values()
        )
        with pytest.raises(MigrationError, match="must be empty"):
            migrate_sqlite_business_data_to_mysql(
                config.DATABASE_PATH,
                target,
                batch_size=500,
            )
        validation_report = migrate_sqlite_business_data_to_mysql(
            config.DATABASE_PATH,
            target,
            batch_size=500,
            validate_only=True,
        )
        assert all(table.matches for table in validation_report.tables)
        application_checks = _application_read_rehearsal(database_name, samples)
        original_max = report.auto_increment["papers"]["max_id"]
        try:
            target.begin()
            with target.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO papers (
                        query, title, authors, summary, published, source,
                        publisher, page_url, status
                    )
                    VALUES ('auto-test', 'auto-test', '', '', '', 'test', '', '', 'available')
                    """
                )
                assert int(cursor.lastrowid) > original_max
        finally:
            target.rollback()
        summary = {
            "source": report.source_before.path,
            "source_sha256": report.source_before.sha256,
            "source_unchanged": report.source_before == report.source_after,
            "counts": {
                table.table: [table.source_count, table.target_count]
                for table in report.tables
            },
            "pk_match": all(
                table.source_pk_hash == table.target_pk_hash for table in report.tables
            ),
            "content_match": all(
                table.source_content_hash == table.target_content_hash
                for table in report.tables
            ),
            "fk_count": report.foreign_key_count,
            "generated_column_errors": report.generated_column_errors,
            "question_hash_errors": report.question_hash_errors,
            "active_dedupe_conflicts": report.source_active_dedupe_conflicts,
            "question_duplicates": report.source_question_duplicates,
            "schema_drift": {
                table: values
                for table, values in report.schema_drift.items()
                if any(values.values())
            },
            "auto_increment": report.auto_increment,
            "pk_ranges": {
                table.table: [table.source_pk_min, table.source_pk_max]
                for table in report.tables
                if table.source_pk_min is not None or table.source_pk_max is not None
            },
            "validate_only": all(
                table.matches for table in validation_report.tables
            ),
            "application_reads": application_checks,
        }
        print("PHASE6A_REHEARSAL=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    assert file_snapshot(config.DATABASE_PATH) == source_before

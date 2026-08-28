import os
import re
from dataclasses import replace
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db
from my_agent_project.mysql_migrations import (
    apply_mysql_migrations,
    discover_mysql_migrations,
    split_mysql_statements,
)


BUSINESS_TABLES = {
    "papers",
    "paper_documents",
    "paper_parent_chunks",
    "paper_chunks",
    "background_jobs",
    "research_tasks",
    "research_tool_calls",
    "research_reports",
    "research_messages",
    "research_task_memories",
    "agent_traces",
    "rag_queries",
    "pdf_text_cache",
    "embedding_cache",
    "llm_response_cache",
    "evaluation_cases",
    "evaluation_runs",
    "evaluation_results",
    "generation_evaluation_runs",
    "generation_evaluation_results",
}


def _initial_schema():
    migrations = discover_mysql_migrations()
    assert [migration.version for migration in migrations] == ["001", "002"]
    return migrations[0]


def _performance_indexes():
    migrations = discover_mysql_migrations()
    assert [migration.version for migration in migrations] == ["001", "002"]
    return migrations[1]


def _table_statements(sql: str) -> list[tuple[str, str]]:
    tables: list[tuple[str, str]] = []
    for statement in split_mysql_statements(sql):
        match = re.match(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)",
            statement,
            flags=re.IGNORECASE,
        )
        if match:
            tables.append((match.group(1), statement))
    return tables


def test_mysql_initial_schema_contains_all_business_tables_in_fk_order() -> None:
    migration = _initial_schema()
    tables = _table_statements(migration.sql)
    table_names = [name for name, _ in tables]

    assert len(table_names) == 20
    assert set(table_names) == BUSINESS_TABLES
    assert "schema_migrations" not in table_names
    positions = {name: index for index, name in enumerate(table_names)}
    for table_name, statement in tables:
        referenced_tables = re.findall(
            r"REFERENCES\s+(\w+)",
            statement,
            flags=re.IGNORECASE,
        )
        for referenced_table in referenced_tables:
            assert positions[referenced_table] < positions[table_name], (
                f"{table_name} appears before FK parent {referenced_table}"
            )


def test_mysql_initial_schema_uses_mysql_types_engine_and_charset() -> None:
    migration = _initial_schema()
    tables = _table_statements(migration.sql)
    normalized = migration.sql.upper()

    assert "AUTOINCREMENT" not in normalized
    assert "INSERT OR IGNORE" not in normalized
    assert "ON CONFLICT" not in normalized
    assert "BEGIN IMMEDIATE" not in normalized
    assert "PRAGMA" not in normalized
    assert normalized.count("ENGINE=INNODB") == 20
    assert normalized.count("DEFAULT CHARACTER SET=UTF8MB4") == 20
    assert "BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in normalized
    assert "DOUBLE NOT NULL" in normalized
    assert " JSON " not in normalized
    for _, statement in tables:
        assert "PRIMARY KEY" in statement.upper()


def test_mysql_schema_uses_bounded_keys_hashes_and_longtext_payloads() -> None:
    sql = _initial_schema().sql

    assert re.search(r"dedupe_key\s+VARCHAR\(255\)", sql)
    assert re.search(r"checkpoint_thread_id\s+VARCHAR\(128\)", sql)
    assert len(re.findall(r"(?:source|content|input|question)_hash\s+CHAR\(64\)", sql)) >= 5
    assert re.search(r"cache_namespace\s+VARCHAR\(512\)", sql)
    assert re.search(r"question\s+LONGTEXT\s+NOT NULL", sql)
    assert re.search(
        r"question_hash.*GENERATED ALWAYS AS \(SHA2\(question, 256\)\) STORED",
        sql,
        flags=re.DOTALL,
    )
    assert "UNIQUE KEY uq_evaluation_cases_paper_question_hash" in sql
    for column in (
        "payload_json",
        "plan_queries_json",
        "conversation_summary",
        "content",
        "vector_json",
        "response_json",
        "metrics_json",
    ):
        assert re.search(rf"\b{column}\s+LONGTEXT", sql)


def test_mysql_schema_active_job_dedupe_matches_sqlite_active_statuses() -> None:
    sql = _initial_schema().sql

    generated_column = re.search(
        r"active_dedupe_key.*?GENERATED ALWAYS AS \((.*?)\) STORED",
        sql,
        flags=re.DOTALL,
    )
    assert generated_column is not None
    expression = generated_column.group(1)
    assert "status IN ('queued', 'running', 'retry_wait')" in expression
    assert "THEN dedupe_key" in expression
    assert "ELSE NULL" in expression
    assert "UNIQUE KEY idx_background_jobs_active_dedupe (active_dedupe_key)" in sql


def test_mysql_schema_uses_datetime_for_all_operational_times() -> None:
    sql = _initial_schema().sql
    time_columns = {
        "downloaded_at",
        "created_at",
        "updated_at",
        "confirmed_at",
        "selected_at",
        "completed_at",
        "conversation_summary_updated_at",
        "task_memory_updated_at",
        "parsed_at",
        "indexed_at",
        "start_time",
        "end_time",
        "started_at",
        "available_at",
        "lease_expires_at",
    }

    for column in time_columns:
        matches = re.findall(
            rf"(?m)^\s*{column.upper()}\s+([A-Z]+(?:\(\d+\))?)",
            sql.upper(),
        )
        assert matches, column
        assert set(matches) == {"DATETIME(6)"}, (column, matches)


def test_mysql_schema_keeps_existing_fk_delete_semantics_and_indexes() -> None:
    sql = _initial_schema().sql

    assert sql.count("FOREIGN KEY") == 19
    assert sql.count("ON DELETE CASCADE") == 19
    assert "ON DELETE SET NULL" not in sql
    assert "ON DELETE RESTRICT" not in sql
    for index_name in (
        "idx_papers_created_at",
        "idx_papers_query",
        "idx_research_tasks_created_at",
        "idx_research_tasks_checkpoint_thread",
        "idx_agent_traces_task_id",
        "idx_agent_traces_thread_node",
        "idx_research_messages_task_id",
        "idx_research_task_memories_task_status",
        "idx_rag_queries_paper_id",
        "idx_background_jobs_ready",
        "idx_background_jobs_rag_query",
        "idx_background_jobs_active_dedupe",
        "idx_evaluation_cases_paper_id",
    ):
        assert index_name in sql


def test_mysql_schema_does_not_contain_langgraph_checkpoint_tables() -> None:
    table_names = {name for name, _ in _table_statements(_initial_schema().sql)}

    assert table_names.isdisjoint(
        {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "writes"}
    )


def test_mysql_performance_indexes_are_additive_and_bounded() -> None:
    sql = _performance_indexes().sql
    normalized = sql.upper()

    assert "ALTER TABLE RESEARCH_MESSAGES" in normalized
    assert "IDX_RESEARCH_MESSAGES_FOLLOWUP_RECENT" in normalized
    assert "IDX_BACKGROUND_JOBS_TASK_TYPE_RECENT" in normalized
    assert "IDX_BACKGROUND_JOBS_PAPER_TYPE_RECENT" in normalized
    assert "LONGTEXT" not in normalized
    assert "FULLTEXT" not in normalized
    assert "DROP INDEX" not in normalized
    assert "CREATE TABLE" not in normalized


def test_mysql_statement_splitter_preserves_semicolons_in_strings() -> None:
    statements = split_mysql_statements(
        """
        -- ignored comment
        CREATE TABLE sample (value VARCHAR(32) DEFAULT 'a;b');
        /* ignored block */ INSERT INTO sample (value) VALUES ('c;d');
        """
    )

    assert statements == [
        "CREATE TABLE sample (value VARCHAR(32) DEFAULT 'a;b')",
        "INSERT INTO sample (value) VALUES ('c;d')",
    ]


class FakeMigrationCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.rows: list[dict[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = " ".join(sql.split()).upper()
        self.connection.statements.append((sql, params))
        if normalized.startswith("SELECT VERSION, CHECKSUM FROM SCHEMA_MIGRATIONS"):
            self.rows = [
                {"version": version, "checksum": checksum}
                for version, checksum in self.connection.applied.items()
            ]
        elif normalized.startswith("INSERT INTO SCHEMA_MIGRATIONS"):
            version, _, checksum = params
            self.connection.applied[str(version)] = str(checksum)

    def fetchall(self):
        return self.rows


class FakeMigrationConnection:
    def __init__(self) -> None:
        self.applied: dict[str, str] = {}
        self.statements: list[tuple[str, object]] = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> FakeMigrationCursor:
        return FakeMigrationCursor(self)

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def test_mysql_migration_runner_records_version_and_is_idempotent() -> None:
    connection = FakeMigrationConnection()

    assert apply_mysql_migrations(connection) == ["001", "002"]
    statement_count = len(connection.statements)
    assert connection.applied == {
        "001": _initial_schema().checksum,
        "002": _performance_indexes().checksum,
    }
    assert apply_mysql_migrations(connection) == []
    assert len(connection.statements) == statement_count + 2
    assert connection.begin_count == 2
    assert connection.rollback_count == 0


def test_mysql_migration_runner_rejects_changed_applied_migration() -> None:
    connection = FakeMigrationConnection()
    migration = _initial_schema()
    connection.applied[migration.version] = "0" * 64

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        apply_mysql_migrations(connection, [replace(migration, sql="SELECT 1")])


def test_mysql_migration_runner_rejects_changed_performance_migration() -> None:
    connection = FakeMigrationConnection()
    connection.applied["001"] = _initial_schema().checksum
    connection.applied["002"] = "0" * 64

    with pytest.raises(RuntimeError, match="002 checksum mismatch"):
        apply_mysql_migrations(connection)


MYSQL_INTEGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(
    not MYSQL_INTEGRATION_ENABLED,
    reason="set PAPER_HUNTER_MYSQL_TEST=1 to run the MySQL schema test",
)
def test_mysql_initial_schema_in_isolated_database(monkeypatch) -> None:
    database_name = f"paper_hunter_phase2_{uuid4().hex}"
    assert re.fullmatch(r"paper_hunter_phase2_[0-9a-f]{32}", database_name)
    admin_connection = pymysql.connect(
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
    database_created = False
    connection = None
    try:
        with admin_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        database_created = True
        monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
        monkeypatch.setattr(db, "BUSINESS_DB_NAME", database_name)
        connection = db.connect()

        assert apply_mysql_migrations(connection) == ["001", "002"]
        assert apply_mysql_migrations(connection) == []

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    TABLE_NAME AS table_name,
                    ENGINE AS engine,
                    TABLE_COLLATION AS table_collation
                FROM information_schema.tables
                WHERE table_schema = %s
                """,
                (database_name,),
            )
            tables = {row["table_name"]: row for row in cursor.fetchall()}
        assert set(tables) == BUSINESS_TABLES | {"schema_migrations"}
        assert all(row["engine"] == "InnoDB" for row in tables.values())
        assert all(
            str(row["table_collation"]).startswith("utf8mb4_")
            for row in tables.values()
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT TABLE_NAME AS table_name
                FROM information_schema.table_constraints
                WHERE constraint_schema = %s AND constraint_type = 'PRIMARY KEY'
                """,
                (database_name,),
            )
            primary_key_tables = {row["table_name"] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT CONSTRAINT_NAME AS constraint_name
                FROM information_schema.referential_constraints
                WHERE constraint_schema = %s
                """,
                (database_name,),
            )
            foreign_keys = {row["constraint_name"] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT
                    TABLE_NAME AS table_name,
                    INDEX_NAME AS index_name
                FROM information_schema.statistics
                WHERE table_schema = %s
                """,
                (database_name,),
            )
            indexes = {
                (row["table_name"], row["index_name"])
                for row in cursor.fetchall()
            }
        assert primary_key_tables == BUSINESS_TABLES | {"schema_migrations"}
        assert len(foreign_keys) == 19
        assert ("background_jobs", "idx_background_jobs_active_dedupe") in indexes
        assert ("background_jobs", "idx_background_jobs_ready") in indexes
        assert (
            "background_jobs",
            "idx_background_jobs_task_type_recent",
        ) in indexes
        assert (
            "background_jobs",
            "idx_background_jobs_paper_type_recent",
        ) in indexes
        assert (
            "research_messages",
            "idx_research_messages_followup_recent",
        ) in indexes
        assert ("research_tasks", "idx_research_tasks_checkpoint_thread") in indexes

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    TABLE_NAME AS table_name,
                    COLUMN_NAME AS column_name,
                    DATA_TYPE AS data_type,
                    EXTRA AS extra
                FROM information_schema.columns
                WHERE table_schema = %s
                """,
                (database_name,),
            )
            columns = {
                (row["table_name"], row["column_name"]): row
                for row in cursor.fetchall()
            }
        assert columns[("background_jobs", "available_at")]["data_type"] == "datetime"
        assert columns[("papers", "created_at")]["data_type"] == "datetime"
        assert "STORED GENERATED" in columns[
            ("background_jobs", "active_dedupe_key")
        ]["extra"].upper()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO papers (
                    query, title, authors, summary, published, source,
                    publisher, page_url, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "中文检索",
                    "中文论文标题",
                    "测试作者",
                    "摘要",
                    "2026",
                    "test",
                    "测试出版社",
                    "https://example.test/paper",
                    "available",
                ),
            )
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute("SELECT title FROM papers LIMIT 1")
            assert cursor.fetchone()["title"] == "中文论文标题"

        job_values = (
            "test_job",
            "same-key",
            "2026-08-27 00:00:00",
            "2026-08-27 00:00:00",
            "2026-08-27 00:00:00",
        )
        insert_job_sql = """
            INSERT INTO background_jobs (
                job_type, dedupe_key, status, available_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """
        with connection.cursor() as cursor:
            cursor.execute(insert_job_sql, (job_values[0], job_values[1], "queued", *job_values[2:]))
        connection.commit()
        with pytest.raises(pymysql.err.IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    insert_job_sql,
                    (job_values[0], job_values[1], "running", *job_values[2:]),
                )
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE background_jobs SET status = 'completed' WHERE dedupe_key = %s",
                (job_values[1],),
            )
            cursor.execute(
                insert_job_sql,
                (job_values[0], job_values[1], "failed", *job_values[2:]),
            )
            cursor.execute(
                insert_job_sql,
                (job_values[0], job_values[1], "running", *job_values[2:]),
            )
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM background_jobs WHERE dedupe_key = %s ORDER BY id",
                (job_values[1],),
            )
            assert [row["status"] for row in cursor.fetchall()] == [
                "completed",
                "failed",
                "running",
            ]
    finally:
        if connection is not None:
            connection.close()
        if database_created:
            with admin_connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin_connection.close()

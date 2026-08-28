import os
import sqlite3
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db


class FakeCursor:
    def __init__(self, statements: list[tuple[str, object]]) -> None:
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        self.statements.append((sql, params))


class FakeMySQLConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.statements)

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1


def _select_mysql_backend(monkeypatch) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    monkeypatch.setattr(db, "BUSINESS_DB_HOST", "mysql.example.test")
    monkeypatch.setattr(db, "BUSINESS_DB_PORT", 3307)
    monkeypatch.setattr(db, "BUSINESS_DB_NAME", "paper_hunter_test")
    monkeypatch.setattr(db, "BUSINESS_DB_USER", "paper_hunter")
    monkeypatch.setattr(db, "BUSINESS_DB_PASSWORD", "test-password")
    monkeypatch.setattr(db, "BUSINESS_DB_CONNECT_TIMEOUT", 4)
    monkeypatch.setattr(db, "BUSINESS_DB_READ_TIMEOUT", 5)
    monkeypatch.setattr(db, "BUSINESS_DB_WRITE_TIMEOUT", 6)


def test_business_database_config_defaults_to_mysql() -> None:
    settings = config.load_business_database_config({})

    assert settings.backend == "mysql"
    assert settings.port == 3306
    assert settings.connect_timeout == 10
    assert settings.read_timeout == 30
    assert settings.write_timeout == 30


def test_business_database_config_parses_mysql_settings() -> None:
    settings = config.load_business_database_config(
        {
            "BUSINESS_DB_BACKEND": " MySQL ",
            "BUSINESS_DB_HOST": "db.internal",
            "BUSINESS_DB_PORT": "3307",
            "BUSINESS_DB_NAME": "papers",
            "BUSINESS_DB_USER": "worker",
            "BUSINESS_DB_PASSWORD": "secret with spaces ",
            "BUSINESS_DB_CONNECT_TIMEOUT": "7",
            "BUSINESS_DB_READ_TIMEOUT": "31",
            "BUSINESS_DB_WRITE_TIMEOUT": "32",
        }
    )

    assert settings == config.BusinessDatabaseConfig(
        backend="mysql",
        host="db.internal",
        port=3307,
        name="papers",
        user="worker",
        password="secret with spaces ",
        connect_timeout=7,
        read_timeout=31,
        write_timeout=32,
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"BUSINESS_DB_BACKEND": "postgres"}, "BUSINESS_DB_BACKEND"),
        ({"BUSINESS_DB_PORT": "not-a-port"}, "BUSINESS_DB_PORT"),
        ({"BUSINESS_DB_CONNECT_TIMEOUT": "0"}, "BUSINESS_DB_CONNECT_TIMEOUT"),
    ],
)
def test_business_database_config_rejects_invalid_values(values, message) -> None:
    with pytest.raises(ValueError, match=message):
        config.load_business_database_config(values)


def test_sqlite_backend_preserves_row_api_and_explicit_transaction(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "business.db")

    with db.transaction() as conn:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (value) VALUES (?)", ("中文",))

    with db.read_connection() as conn:
        row = conn.execute("SELECT value FROM sample").fetchone()

    assert row["value"] == "中文"
    assert dict(row) == {"value": "中文"}


def test_sqlite_transaction_rolls_back_on_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "business.db")
    with db.transaction() as conn:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="stop"):
        with db.transaction() as conn:
            conn.execute("INSERT INTO sample (value) VALUES (?)", ("rollback",))
            raise RuntimeError("stop")

    with db.read_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM sample").fetchone()["count"]
    assert count == 0


def test_mysql_connect_uses_dict_cursor_utf8mb4_and_utc(monkeypatch) -> None:
    _select_mysql_backend(monkeypatch)
    fake_connection = FakeMySQLConnection()
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return fake_connection

    monkeypatch.setattr(db.pymysql, "connect", fake_connect)

    connection = db.connect()

    assert isinstance(connection, db.MySQLConnectionAdapter)
    assert connection.raw_connection is fake_connection
    assert captured == {
        "host": "mysql.example.test",
        "port": 3307,
        "user": "paper_hunter",
        "password": "test-password",
        "database": "paper_hunter_test",
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": db.pymysql.cursors.DictCursor,
        "client_flag": db.CLIENT.FOUND_ROWS,
        "connect_timeout": 4,
        "read_timeout": 5,
        "write_timeout": 6,
    }
    assert fake_connection.statements == [
        ("SET SESSION time_zone = '+00:00'", None),
        ("SET SESSION default_storage_engine = 'InnoDB'", None),
    ]


def test_mysql_connection_failure_never_falls_back_to_sqlite(monkeypatch) -> None:
    _select_mysql_backend(monkeypatch)
    sqlite_calls: list[object] = []

    def reject_mysql(**kwargs):
        raise db.pymysql.OperationalError(2003, "connection refused")

    def record_sqlite(*args, **kwargs):
        sqlite_calls.append((args, kwargs))
        raise AssertionError("SQLite fallback must not be attempted")

    monkeypatch.setattr(db.pymysql, "connect", reject_mysql)
    monkeypatch.setattr(db.sqlite3, "connect", record_sqlite)

    with pytest.raises(db.pymysql.OperationalError, match="connection refused"):
        db.connect()

    assert sqlite_calls == []


def test_mysql_backend_does_not_run_sqlite_schema_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")

    with pytest.raises(RuntimeError, match="migrations/mysql"):
        db.init_db()


def test_business_database_runtime_initialization_is_backend_aware(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(db, "init_db", lambda: calls.append("sqlite"))
    monkeypatch.setattr(
        db,
        "validate_mysql_business_schema",
        lambda: calls.append("mysql"),
    )

    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    db.initialize_business_database()
    assert calls == ["mysql"]

    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")
    db.initialize_business_database()
    assert calls == ["mysql", "sqlite"]


def test_mysql_transaction_commits_and_closes(monkeypatch) -> None:
    _select_mysql_backend(monkeypatch)
    fake_connection = FakeMySQLConnection()
    monkeypatch.setattr(db, "connect", lambda: fake_connection)

    with db.transaction() as connection:
        assert connection is fake_connection

    assert fake_connection.begin_count == 1
    assert fake_connection.commit_count == 1
    assert fake_connection.rollback_count == 0
    assert fake_connection.close_count == 1


def test_mysql_transaction_rolls_back_and_reraises(monkeypatch) -> None:
    _select_mysql_backend(monkeypatch)
    fake_connection = FakeMySQLConnection()
    monkeypatch.setattr(db, "connect", lambda: fake_connection)

    with pytest.raises(RuntimeError, match="stop"):
        with db.transaction():
            raise RuntimeError("stop")

    assert fake_connection.begin_count == 1
    assert fake_connection.commit_count == 0
    assert fake_connection.rollback_count == 1
    assert fake_connection.close_count == 1


def test_business_and_checkpoint_database_configuration_are_independent() -> None:
    settings = config.load_business_database_config(
        {
            "BUSINESS_DB_BACKEND": "mysql",
            "BUSINESS_DB_HOST": "db.internal",
            "BUSINESS_DB_NAME": "paper_hunter",
            "BUSINESS_DB_USER": "worker",
        }
    )

    assert settings.backend == "mysql"
    assert config.LANGGRAPH_CHECKPOINT_PATH.name == "langgraph_checkpoints.db"
    assert config.LANGGRAPH_CHECKPOINT_PATH != config.DATABASE_PATH


MYSQL_INTEGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(
    not MYSQL_INTEGRATION_ENABLED,
    reason="set PAPER_HUNTER_MYSQL_TEST=1 to run the MySQL connectivity test",
)
def test_mysql_connectivity_dict_row_transactions_and_utf8mb4(monkeypatch) -> None:
    database_name = f"paper_hunter_phase1_{uuid4().hex}"
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
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    monkeypatch.setattr(db, "BUSINESS_DB_NAME", database_name)
    table_name = "paper_hunter_phase1_connectivity_smoke"

    try:
        with admin_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        database_created = True
        with db.transaction() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        value VARCHAR(255) NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cursor.execute(f"DELETE FROM {table_name}")

        with db.transaction() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {table_name} (value) VALUES (%s)",
                    ("中文连接测试",),
                )

        with db.read_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT value FROM {table_name} ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
        assert row["value"] == "中文连接测试"
        assert dict(row) == {"value": "中文连接测试"}

        with pytest.raises(RuntimeError, match="force rollback"):
            with db.transaction() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"INSERT INTO {table_name} (value) VALUES (%s)",
                        ("不得提交",),
                    )
                raise RuntimeError("force rollback")

        with db.read_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS count FROM {table_name} WHERE value = %s",
                    ("不得提交",),
                )
                row = cursor.fetchone()
        assert row["count"] == 0
    finally:
        if database_created:
            with admin_connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin_connection.close()

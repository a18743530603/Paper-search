import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import BASE_DIR, BUSINESS_DB_BACKEND
from .db import connect


MYSQL_MIGRATIONS_DIR = BASE_DIR / "migrations" / "mysql"
MIGRATION_FILENAME_PATTERN = re.compile(
    r"^(?P<version>\d{3,})_(?P<name>[a-z0-9_]+)\.sql$"
)
SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name VARCHAR(255) NOT NULL,
    checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


@dataclass(frozen=True)
class MySQLMigration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str


def discover_mysql_migrations(
    migrations_dir: Path = MYSQL_MIGRATIONS_DIR,
) -> list[MySQLMigration]:
    migrations: list[MySQLMigration] = []
    seen_versions: set[str] = set()
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_FILENAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise ValueError(f"Invalid MySQL migration filename: {path.name}")
        version = match.group("version")
        if version in seen_versions:
            raise ValueError(f"Duplicate MySQL migration version: {version}")
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            MySQLMigration(
                version=version,
                name=match.group("name"),
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
        seen_versions.add(version)
    if not migrations:
        raise RuntimeError(f"No MySQL migrations found in {migrations_dir}")
    migrations.sort(key=lambda migration: int(migration.version))
    return migrations


def split_mysql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
                current.append(char)
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote is not None:
            current.append(char)
            if char == "\\" and next_char:
                current.append(next_char)
                index += 2
                continue
            if char == quote:
                if next_char == quote:
                    current.append(next_char)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char == "-" and next_char == "-":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quote is not None or block_comment:
        raise ValueError("Unterminated quote or block comment in MySQL migration")
    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _applied_migrations(connection) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT version, checksum FROM schema_migrations")
        return {
            str(row["version"]): str(row["checksum"])
            for row in cursor.fetchall()
        }


def apply_mysql_migrations(
    connection,
    migrations: Iterable[MySQLMigration] | None = None,
) -> list[str]:
    selected = list(
        discover_mysql_migrations() if migrations is None else migrations
    )
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_MIGRATIONS_DDL)
    connection.commit()
    applied = _applied_migrations(connection)
    newly_applied: list[str] = []
    for migration in selected:
        existing_checksum = applied.get(migration.version)
        if existing_checksum is not None:
            if existing_checksum != migration.checksum:
                raise RuntimeError(
                    f"MySQL migration {migration.version} checksum mismatch"
                )
            continue
        transaction_started = False
        try:
            connection.begin()
            transaction_started = True
            with connection.cursor() as cursor:
                for statement in split_mysql_statements(migration.sql):
                    cursor.execute(statement)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
            connection.commit()
        except BaseException:
            if transaction_started:
                connection.rollback()
            raise
        applied[migration.version] = migration.checksum
        newly_applied.append(migration.version)
    return newly_applied


def migrate_configured_mysql_database() -> list[str]:
    if BUSINESS_DB_BACKEND != "mysql":
        raise RuntimeError("BUSINESS_DB_BACKEND must be mysql to run MySQL migrations")
    connection = connect()
    try:
        return apply_mysql_migrations(connection)
    finally:
        connection.close()

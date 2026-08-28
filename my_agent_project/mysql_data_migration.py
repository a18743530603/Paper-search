from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import pymysql


BUSINESS_TABLES = (
    "papers",
    "research_tasks",
    "pdf_text_cache",
    "embedding_cache",
    "llm_response_cache",
    "evaluation_runs",
    "paper_documents",
    "paper_parent_chunks",
    "paper_chunks",
    "rag_queries",
    "evaluation_cases",
    "research_tool_calls",
    "agent_traces",
    "research_reports",
    "research_messages",
    "research_task_memories",
    "background_jobs",
    "evaluation_results",
    "generation_evaluation_runs",
    "generation_evaluation_results",
)
FORMAL_MYSQL_DATABASE = "paper_hunter"
DEFAULT_BATCH_SIZE = 500
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CRITICAL_CONTENT_TABLES = frozenset(
    {
        "papers",
        "research_tasks",
        "research_reports",
        "research_messages",
        "research_task_memories",
        "paper_chunks",
        "pdf_text_cache",
        "embedding_cache",
        "llm_response_cache",
    }
)


class MigrationError(RuntimeError):
    pass


class MigrationDataError(MigrationError):
    pass


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    size: int
    modified_ns: int
    sha256: str


@dataclass(frozen=True)
class TargetColumn:
    name: str
    data_type: str
    column_type: str
    nullable: bool
    default: Any
    extra: str
    ordinal: int
    max_length: int | None

    @property
    def generated(self) -> bool:
        return "GENERATED" in self.extra.upper()

    @property
    def unsigned(self) -> bool:
        return "unsigned" in self.column_type.lower()


@dataclass
class TableMigrationResult:
    table: str
    source_count: int
    target_count: int
    source_pk_min: Any
    source_pk_max: Any
    target_pk_min: Any
    target_pk_max: Any
    source_pk_hash: str
    target_pk_hash: str
    source_content_hash: str | None
    target_content_hash: str | None
    inserted_columns: list[str]
    generated_columns: list[str]
    batches: int

    @property
    def matches(self) -> bool:
        return (
            self.source_count == self.target_count
            and self.source_pk_min == self.target_pk_min
            and self.source_pk_max == self.target_pk_max
            and self.source_pk_hash == self.target_pk_hash
            and self.source_content_hash == self.target_content_hash
        )


@dataclass
class MigrationReport:
    source_before: FileSnapshot
    source_after: FileSnapshot
    target_database: str
    schema_drift: dict[str, dict[str, list[str]]]
    tables: list[TableMigrationResult]
    foreign_key_checks_enabled: bool
    foreign_key_count: int
    foreign_key_orphans: dict[str, int]
    generated_column_errors: dict[str, int]
    question_hash_errors: int
    source_question_duplicates: list[dict[str, Any]]
    source_active_dedupe_conflicts: list[dict[str, Any]]
    auto_increment: dict[str, dict[str, int]]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_unchanged"] = self.source_before == self.source_after
        result["all_tables_match"] = all(table.matches for table in self.tables)
        return result


def guard_target_database(database_name: str) -> None:
    normalized = database_name.strip().lower()
    if not normalized:
        raise MigrationError("Target MySQL database name is required")
    if normalized == FORMAL_MYSQL_DATABASE:
        raise MigrationError("Refusing to migrate into formal MySQL database paper_hunter")
    if not IDENTIFIER_PATTERN.fullmatch(database_name):
        raise MigrationError(f"Unsafe target MySQL database name: {database_name!r}")


def file_snapshot(path: Path) -> FileSnapshot:
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    with resolved.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    stat = resolved.stat()
    return FileSnapshot(
        path=str(resolved),
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


def open_sqlite_source_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve(strict=True)
    connection = sqlite3.connect(
        resolved.as_uri() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def _identifier(value: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise MigrationError(f"Unsafe SQL identifier: {value!r}")
    return value


def _quoted_sqlite(value: str) -> str:
    return f'"{_identifier(value)}"'


def _quoted_mysql(value: str) -> str:
    return f"`{_identifier(value)}`"


def _source_columns(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(connection.execute(f"PRAGMA table_info({_identifier(table)})"))


def _source_primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = _source_columns(connection, table)
    return [
        str(row["name"])
        for row in sorted(columns, key=lambda row: int(row["pk"]) or 10_000)
        if int(row["pk"]) > 0
    ]


def _target_database(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS database_name")
        row = cursor.fetchone()
    name = str(row["database_name"] or "")
    guard_target_database(name)
    return name


def _target_columns(connection, database: str, table: str) -> list[TargetColumn]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COLUMN_NAME AS column_name,
                DATA_TYPE AS data_type,
                COLUMN_TYPE AS column_type,
                IS_NULLABLE AS is_nullable,
                COLUMN_DEFAULT AS column_default,
                EXTRA AS extra,
                ORDINAL_POSITION AS ordinal_position,
                CHARACTER_MAXIMUM_LENGTH AS character_maximum_length
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (database, table),
        )
        rows = cursor.fetchall()
    return [
        TargetColumn(
            name=str(row["column_name"]),
            data_type=str(row["data_type"]).lower(),
            column_type=str(row["column_type"]),
            nullable=str(row["is_nullable"]).upper() == "YES",
            default=row["column_default"],
            extra=str(row["extra"] or ""),
            ordinal=int(row["ordinal_position"]),
            max_length=(
                int(row["character_maximum_length"])
                if row["character_maximum_length"] is not None
                else None
            ),
        )
        for row in rows
    ]


def audit_schema_drift(
    source: sqlite3.Connection,
    target,
    database: str,
) -> tuple[dict[str, dict[str, list[str]]], dict[str, list[TargetColumn]]]:
    source_tables = {
        str(row["name"])
        for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    target_columns: dict[str, list[TargetColumn]] = {}
    drift: dict[str, dict[str, list[str]]] = {}
    for table in BUSINESS_TABLES:
        if table not in source_tables:
            raise MigrationError(f"Source SQLite business table is missing: {table}")
        columns = _target_columns(target, database, table)
        if not columns:
            raise MigrationError(f"Target MySQL business table is missing: {table}")
        target_columns[table] = columns
        source_names = {str(row["name"]) for row in _source_columns(source, table)}
        target_names = {column.name for column in columns}
        generated = {column.name for column in columns if column.generated}
        legacy = sorted(source_names - target_names)
        populated_legacy: list[str] = []
        for column_name in legacy:
            row = source.execute(
                f"SELECT COUNT(*) AS value_count FROM {_quoted_sqlite(table)} "
                f"WHERE {_quoted_sqlite(column_name)} IS NOT NULL"
            ).fetchone()
            value_count = int(row["value_count"])
            if value_count:
                populated_legacy.append(f"{column_name}:{value_count}")
        if populated_legacy:
            raise MigrationDataError(
                f"Source {table} has populated legacy columns with no target mapping: "
                f"{populated_legacy}"
            )
        target_only = sorted(target_names - source_names - generated)
        generated_only = sorted(generated - source_names)
        required_missing = sorted(
            column.name
            for column in columns
            if column.name in target_only
            and not column.nullable
            and column.default is None
            and "auto_increment" not in column.extra.lower()
        )
        if required_missing:
            raise MigrationError(
                f"Source {table} lacks required target columns: {required_missing}"
            )
        drift[table] = {
            "legacy_source_columns": legacy,
            "populated_legacy_source_columns": populated_legacy,
            "target_defaulted_or_nullable_columns": target_only,
            "target_generated_columns": generated_only,
        }
    return drift, target_columns


def _row_identity(row: sqlite3.Row | dict[str, Any], pk_columns: list[str]) -> str:
    values = [row[column] for column in pk_columns]
    return json.dumps(values, ensure_ascii=False, default=str)


def convert_datetime(value: Any, *, table: str, row_id: str, column: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise MigrationDataError(
                f"Invalid datetime: table={table} row={row_id} column={column} category=empty_string"
            )
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise MigrationDataError(
                f"Invalid datetime: table={table} row={row_id} column={column} value={value!r}"
            ) from exc
    else:
        raise MigrationDataError(
            f"Invalid datetime: table={table} row={row_id} column={column} category={type(value).__name__}"
        )
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


INTEGER_TYPES = frozenset({"tinyint", "smallint", "mediumint", "int", "integer", "bigint"})
FLOAT_TYPES = frozenset({"float", "double", "real", "decimal", "numeric"})
STRING_TYPES = frozenset({"char", "varchar", "tinytext", "text", "mediumtext", "longtext"})


def _convert_value(
    value: Any,
    column: TargetColumn,
    *,
    table: str,
    row_id: str,
) -> Any:
    if value is None:
        if not column.nullable:
            raise MigrationDataError(
                f"Invalid NULL: table={table} row={row_id} column={column.name}"
            )
        return None
    if column.data_type in {"datetime", "timestamp", "date"}:
        return convert_datetime(value, table=table, row_id=row_id, column=column.name)
    if column.data_type in INTEGER_TYPES:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MigrationDataError(
                f"Invalid integer: table={table} row={row_id} column={column.name} category={type(value).__name__}"
            )
        if column.unsigned and value < 0:
            raise MigrationDataError(
                f"Invalid unsigned integer: table={table} row={row_id} column={column.name} value={value}"
            )
        return value
    if column.data_type in FLOAT_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MigrationDataError(
                f"Invalid numeric: table={table} row={row_id} column={column.name} category={type(value).__name__}"
            )
        converted = float(value)
        if not math.isfinite(converted):
            raise MigrationDataError(
                f"Invalid numeric: table={table} row={row_id} column={column.name} category=non_finite"
            )
        return converted
    if column.data_type in STRING_TYPES:
        if not isinstance(value, str):
            raise MigrationDataError(
                f"Invalid text: table={table} row={row_id} column={column.name} category={type(value).__name__}"
            )
        if column.max_length is not None and len(value) > column.max_length:
            raise MigrationDataError(
                f"Text too long: table={table} row={row_id} column={column.name} length={len(value)} max={column.max_length}"
            )
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return value


def _converted_row(
    row: sqlite3.Row,
    columns: list[TargetColumn],
    pk_columns: list[str],
    table: str,
) -> tuple[Any, ...]:
    row_id = _row_identity(row, pk_columns)
    return tuple(
        _convert_value(row[column.name], column, table=table, row_id=row_id)
        for column in columns
    )


def _source_batches(
    source: sqlite3.Connection,
    table: str,
    column_names: list[str],
    pk_columns: list[str],
    batch_size: int,
) -> Iterator[list[sqlite3.Row]]:
    selected = ", ".join(_quoted_sqlite(column) for column in column_names)
    ordered = ", ".join(_quoted_sqlite(column) for column in pk_columns)
    last_key: tuple[Any, ...] | None = None
    while True:
        parameters: list[Any] = []
        where = ""
        if last_key is not None:
            if len(pk_columns) == 1:
                where = f" WHERE {_quoted_sqlite(pk_columns[0])} > ?"
            else:
                key_expression = ", ".join(
                    _quoted_sqlite(column) for column in pk_columns
                )
                placeholders = ", ".join("?" for _ in pk_columns)
                where = f" WHERE ({key_expression}) > ({placeholders})"
            parameters.extend(last_key)
        parameters.append(batch_size)
        rows = list(
            source.execute(
                f"SELECT {selected} FROM {_quoted_sqlite(table)}{where} "
                f"ORDER BY {ordered} LIMIT ?",
                tuple(parameters),
            )
        )
        if not rows:
            return
        yield rows
        last = rows[-1]
        last_key = tuple(last[column] for column in pk_columns)


def _assert_empty_target(connection) -> None:
    nonempty: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in BUSINESS_TABLES:
            cursor.execute(f"SELECT COUNT(*) AS row_count FROM {_quoted_mysql(table)}")
            count = int(cursor.fetchone()["row_count"])
            if count:
                nonempty[table] = count
    if nonempty:
        raise MigrationError(f"Target business tables must be empty: {nonempty}")


def _source_integrity_preflight(source: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active_conflicts = [
        dict(row)
        for row in source.execute(
            """
            SELECT dedupe_key, GROUP_CONCAT(id) AS job_ids, COUNT(*) AS duplicate_count
            FROM background_jobs
            WHERE status IN ('queued', 'running', 'retry_wait')
            GROUP BY dedupe_key
            HAVING COUNT(*) > 1
            ORDER BY dedupe_key
            """
        )
    ]
    question_duplicates = [
        dict(row)
        for row in source.execute(
            """
            SELECT paper_id, question, GROUP_CONCAT(id) AS case_ids,
                   COUNT(*) AS duplicate_count
            FROM evaluation_cases
            GROUP BY paper_id, question
            HAVING COUNT(*) > 1
            ORDER BY paper_id, question
            """
        )
    ]
    if active_conflicts:
        raise MigrationDataError(
            f"Source has conflicting active dedupe keys: {active_conflicts}"
        )
    if question_duplicates:
        raise MigrationDataError(
            f"Source has duplicate evaluation questions: {question_duplicates}"
        )
    return active_conflicts, question_duplicates


def migrate_table(
    source: sqlite3.Connection,
    target,
    table: str,
    target_columns: list[TargetColumn],
    *,
    batch_size: int,
    fail_after_batch: tuple[str, int] | None = None,
) -> tuple[int, list[str], list[str]]:
    source_names = {str(row["name"]) for row in _source_columns(source, table)}
    insert_columns = [
        column for column in target_columns
        if column.name in source_names and not column.generated
    ]
    generated_columns = [column.name for column in target_columns if column.generated]
    pk_columns = _source_primary_key(source, table)
    if not pk_columns:
        raise MigrationError(f"Source table has no stable primary key: {table}")
    names = [column.name for column in insert_columns]
    sql = (
        f"INSERT INTO {_quoted_mysql(table)} "
        f"({', '.join(_quoted_mysql(name) for name in names)}) VALUES "
        f"({', '.join('%s' for _ in names)})"
    )
    batches = 0
    for rows in _source_batches(source, table, names, pk_columns, batch_size):
        converted = [
            _converted_row(row, insert_columns, pk_columns, table) for row in rows
        ]
        try:
            target.begin()
            with target.cursor() as cursor:
                cursor.executemany(sql, converted)
            batches += 1
            if fail_after_batch == (table, batches):
                raise MigrationError(
                    f"Injected migration failure: table={table} batch={batches}"
                )
            target.commit()
        except BaseException:
            target.rollback()
            raise
    return batches, names, generated_columns


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def _hash_rows(rows: Iterable[Any], columns: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = json.dumps(
            [_canonical(row[column]) for column in columns],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _ordered_source_rows(
    source: sqlite3.Connection,
    table: str,
    columns: list[str],
    pk_columns: list[str],
) -> Iterator[dict[str, Any]]:
    selected = ", ".join(_quoted_sqlite(column) for column in columns)
    ordered = ", ".join(_quoted_sqlite(column) for column in pk_columns)
    cursor = source.execute(
        f"SELECT {selected} FROM {_quoted_sqlite(table)} ORDER BY {ordered}"
    )
    while rows := cursor.fetchmany(DEFAULT_BATCH_SIZE):
        for row in rows:
            yield dict(row)


def _ordered_target_rows(
    target,
    table: str,
    columns: list[str],
    pk_columns: list[str],
) -> Iterator[dict[str, Any]]:
    selected = ", ".join(_quoted_mysql(column) for column in columns)
    ordered = ", ".join(_quoted_mysql(column) for column in pk_columns)
    with target.cursor(pymysql.cursors.SSDictCursor) as cursor:
        cursor.execute(
            f"SELECT {selected} FROM {_quoted_mysql(table)} ORDER BY {ordered}"
        )
        while rows := cursor.fetchmany(DEFAULT_BATCH_SIZE):
            yield from rows


def _source_values_for_hash(
    rows: Iterable[dict[str, Any]],
    columns: list[TargetColumn],
    pk_columns: list[str],
    table: str,
) -> Iterator[dict[str, Any]]:
    for row in rows:
        row_id = json.dumps([row[column] for column in pk_columns], default=str)
        yield {
            column.name: _convert_value(
                row[column.name], column, table=table, row_id=row_id
            )
            for column in columns
        }


def _pk_summary(
    connection,
    table: str,
    pk_columns: list[str],
    *,
    mysql: bool,
) -> tuple[Any, Any, str]:
    quote = _quoted_mysql if mysql else _quoted_sqlite
    if len(pk_columns) == 1:
        pk = quote(pk_columns[0])
        sql = f"SELECT MIN({pk}) AS min_key, MAX({pk}) AS max_key FROM {quote(table)}"
        if mysql:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                bounds = cursor.fetchone()
        else:
            bounds = connection.execute(sql).fetchone()
        minimum, maximum = bounds["min_key"], bounds["max_key"]
    else:
        minimum = maximum = None
    columns_sql = ", ".join(quote(column) for column in pk_columns)
    order_sql = ", ".join(quote(column) for column in pk_columns)
    if mysql:
        rows = _ordered_target_rows(connection, table, pk_columns, pk_columns)
    else:
        rows = _ordered_source_rows(connection, table, pk_columns, pk_columns)
    return minimum, maximum, _hash_rows(rows, pk_columns)


def validate_table(
    source: sqlite3.Connection,
    target,
    table: str,
    target_columns: list[TargetColumn],
    inserted_columns: list[str],
    generated_columns: list[str],
    batches: int,
) -> TableMigrationResult:
    source_count = int(
        source.execute(f"SELECT COUNT(*) FROM {_quoted_sqlite(table)}").fetchone()[0]
    )
    with target.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) AS row_count FROM {_quoted_mysql(table)}")
        target_count = int(cursor.fetchone()["row_count"])
    pk_columns = _source_primary_key(source, table)
    source_min, source_max, source_pk_hash = _pk_summary(
        source, table, pk_columns, mysql=False
    )
    target_min, target_max, target_pk_hash = _pk_summary(
        target, table, pk_columns, mysql=True
    )
    source_content_hash = target_content_hash = None
    if table in CRITICAL_CONTENT_TABLES:
        columns_by_name = {column.name: column for column in target_columns}
        hash_columns = [columns_by_name[name] for name in inserted_columns]
        source_rows = _source_values_for_hash(
            _ordered_source_rows(source, table, inserted_columns, pk_columns),
            hash_columns,
            pk_columns,
            table,
        )
        source_content_hash = _hash_rows(source_rows, inserted_columns)
        target_content_hash = _hash_rows(
            _ordered_target_rows(target, table, inserted_columns, pk_columns),
            inserted_columns,
        )
    result = TableMigrationResult(
        table=table,
        source_count=source_count,
        target_count=target_count,
        source_pk_min=source_min,
        source_pk_max=source_max,
        target_pk_min=target_min,
        target_pk_max=target_max,
        source_pk_hash=source_pk_hash,
        target_pk_hash=target_pk_hash,
        source_content_hash=source_content_hash,
        target_content_hash=target_content_hash,
        inserted_columns=inserted_columns,
        generated_columns=generated_columns,
        batches=batches,
    )
    if not result.matches:
        raise MigrationError(f"Post-migration validation failed for {table}: {result}")
    return result


def validate_foreign_keys(target, database: str) -> tuple[bool, int, dict[str, int]]:
    with target.cursor() as cursor:
        cursor.execute("SELECT @@FOREIGN_KEY_CHECKS AS foreign_key_checks")
        enabled = int(cursor.fetchone()["foreign_key_checks"]) == 1
        cursor.execute(
            """
            SELECT
                k.CONSTRAINT_NAME AS constraint_name,
                k.TABLE_NAME AS child_table,
                k.COLUMN_NAME AS child_column,
                k.REFERENCED_TABLE_NAME AS parent_table,
                k.REFERENCED_COLUMN_NAME AS parent_column
            FROM information_schema.KEY_COLUMN_USAGE AS k
            WHERE k.CONSTRAINT_SCHEMA = %s
              AND k.REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION
            """,
            (database,),
        )
        constraints = cursor.fetchall()
        orphans: dict[str, int] = {}
        for constraint in constraints:
            child_table = _quoted_mysql(str(constraint["child_table"]))
            child_column = _quoted_mysql(str(constraint["child_column"]))
            parent_table = _quoted_mysql(str(constraint["parent_table"]))
            parent_column = _quoted_mysql(str(constraint["parent_column"]))
            cursor.execute(
                f"SELECT COUNT(*) AS orphan_count FROM {child_table} AS child "
                f"LEFT JOIN {parent_table} AS parent "
                f"ON parent.{parent_column} = child.{child_column} "
                f"WHERE child.{child_column} IS NOT NULL "
                f"AND parent.{parent_column} IS NULL"
            )
            orphans[str(constraint["constraint_name"])] = int(
                cursor.fetchone()["orphan_count"]
            )
    if not enabled:
        raise MigrationError("MySQL FOREIGN_KEY_CHECKS is not enabled")
    bad = {name: count for name, count in orphans.items() if count}
    if bad:
        raise MigrationError(f"Foreign-key validation failed: {bad}")
    return enabled, len(constraints), orphans


def validate_generated_columns(target) -> tuple[dict[str, int], int]:
    with target.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS error_count
            FROM background_jobs
            WHERE NOT (
                active_dedupe_key <=> CASE
                    WHEN status IN ('queued', 'running', 'retry_wait') THEN dedupe_key
                    ELSE NULL
                END
            )
            """
        )
        active_errors = int(cursor.fetchone()["error_count"])
        cursor.execute(
            """
            SELECT COUNT(*) AS error_count
            FROM evaluation_cases
            WHERE question_hash <> SHA2(question, 256)
               OR question_hash IS NULL
            """
        )
        question_errors = int(cursor.fetchone()["error_count"])
    generated = {"background_jobs.active_dedupe_key": active_errors}
    if active_errors or question_errors:
        raise MigrationError(
            f"Generated-column validation failed: {generated}, question_hash={question_errors}"
        )
    return generated, question_errors


def validate_auto_increment(target, database: str) -> dict[str, dict[str, int]]:
    with target.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME AS table_name, AUTO_INCREMENT AS auto_increment
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND AUTO_INCREMENT IS NOT NULL
            ORDER BY TABLE_NAME
            """,
            (database,),
        )
        rows = cursor.fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            table = str(row["table_name"])
            cursor.execute(
                f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {_quoted_mysql(table)}"
            )
            maximum = int(cursor.fetchone()["max_id"])
            next_id = int(row["auto_increment"])
            if next_id <= maximum:
                raise MigrationError(
                    f"AUTO_INCREMENT did not advance: table={table} max={maximum} next={next_id}"
                )
            result[table] = {"max_id": maximum, "next_id": next_id}
    return result


def migrate_sqlite_business_data_to_mysql(
    source_path: Path,
    target,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    validate_only: bool = False,
    fail_after_batch: tuple[str, int] | None = None,
) -> MigrationReport:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    database = _target_database(target)
    before = file_snapshot(source_path)
    source = open_sqlite_source_read_only(source_path)
    try:
        drift, target_columns = audit_schema_drift(source, target, database)
        active_conflicts, question_duplicates = _source_integrity_preflight(source)
        if not validate_only:
            _assert_empty_target(target)
        table_results: list[TableMigrationResult] = []
        for table in BUSINESS_TABLES:
            if validate_only:
                source_names = {
                    str(row["name"]) for row in _source_columns(source, table)
                }
                inserted = [
                    column.name for column in target_columns[table]
                    if column.name in source_names and not column.generated
                ]
                generated = [
                    column.name for column in target_columns[table] if column.generated
                ]
                batches = 0
            else:
                batches, inserted, generated = migrate_table(
                    source,
                    target,
                    table,
                    target_columns[table],
                    batch_size=batch_size,
                    fail_after_batch=fail_after_batch,
                )
            table_results.append(
                validate_table(
                    source,
                    target,
                    table,
                    target_columns[table],
                    inserted,
                    generated,
                    batches,
                )
            )
        fk_enabled, fk_count, fk_orphans = validate_foreign_keys(target, database)
        generated_errors, question_hash_errors = validate_generated_columns(target)
        auto_increment = validate_auto_increment(target, database)
    finally:
        source.rollback()
        source.close()
    after = file_snapshot(source_path)
    if before != after:
        raise MigrationError(
            "Source SQLite snapshot changed during migration rehearsal"
        )
    return MigrationReport(
        source_before=before,
        source_after=after,
        target_database=database,
        schema_drift=drift,
        tables=table_results,
        foreign_key_checks_enabled=fk_enabled,
        foreign_key_count=fk_count,
        foreign_key_orphans=fk_orphans,
        generated_column_errors=generated_errors,
        question_hash_errors=question_hash_errors,
        source_question_duplicates=question_duplicates,
        source_active_dedupe_conflicts=active_conflicts,
        auto_increment=auto_increment,
    )

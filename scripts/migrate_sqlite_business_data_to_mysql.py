import argparse
import json
import sys
from pathlib import Path

import pymysql


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from my_agent_project import config
from my_agent_project.mysql_data_migration import (
    DEFAULT_BATCH_SIZE,
    MigrationError,
    guard_target_database,
    migrate_sqlite_business_data_to_mysql,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a read-only Paper Hunter Business SQLite database into an "
            "already-migrated, empty, non-production MySQL database."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=config.DATABASE_PATH,
        help="SQLite Business Database path (default: configured metadata.db)",
    )
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    guard_target_database(args.target_database)
    connection = pymysql.connect(
        host=config.BUSINESS_DB_HOST,
        port=config.BUSINESS_DB_PORT,
        user=config.BUSINESS_DB_USER,
        password=config.BUSINESS_DB_PASSWORD,
        database=args.target_database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=config.BUSINESS_DB_CONNECT_TIMEOUT,
        read_timeout=config.BUSINESS_DB_READ_TIMEOUT,
        write_timeout=config.BUSINESS_DB_WRITE_TIMEOUT,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        report = migrate_sqlite_business_data_to_mysql(
            args.source,
            connection,
            batch_size=args.batch_size,
            validate_only=args.validate_only,
        )
    finally:
        connection.close()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    try:
        main()
    except (MigrationError, ValueError) as exc:
        raise SystemExit(str(exc)) from None

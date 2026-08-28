# MySQL Business Database migrations

This directory contains versioned migrations for the Paper Hunter Business
Database only. LangGraph checkpoints continue to use their separate SQLite
database and are not represented here.

Requirements:

- MySQL 8.0.13 or newer
- an existing empty database configured through the `BUSINESS_DB_*` variables
- a database user allowed to create InnoDB tables, indexes, generated columns,
  and foreign keys inside that database

Apply pending migrations explicitly:

```powershell
$env:BUSINESS_DB_BACKEND = "mysql"
uv run python scripts/migrate_mysql_schema.py
```

FastAPI and Worker startup do not run these migrations. Applied versions and
SHA-256 checksums are recorded in `schema_migrations`; changing an already
applied migration is rejected.

MySQL integration tests are opt-in because they create and later drop an
isolated database:

```powershell
$env:PAPER_HUNTER_MYSQL_TEST = "1"
uv run python -m pytest tests/test_mysql_schema.py tests/test_business_database.py
```

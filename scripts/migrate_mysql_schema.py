import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from my_agent_project.mysql_migrations import migrate_configured_mysql_database


def main() -> None:
    applied = migrate_configured_mysql_database()
    if applied:
        print(f"Applied MySQL migrations: {', '.join(applied)}")
    else:
        print("MySQL Business Database schema is already current.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None

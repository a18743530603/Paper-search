import csv
import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .config import DATABASE_PATH, EXPORTS_DIR, ensure_runtime_dirs
from .schemas import PaperCandidate


SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    summary TEXT NOT NULL,
    published TEXT NOT NULL,
    source TEXT NOT NULL,
    publisher TEXT NOT NULL DEFAULT '',
    doi TEXT,
    page_url TEXT NOT NULL,
    pdf_url TEXT,
    local_path TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_papers_created_at ON papers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_papers_query ON papers(query);
"""


def connect() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        if "publisher" not in columns:
            conn.execute("ALTER TABLE papers ADD COLUMN publisher TEXT NOT NULL DEFAULT ''")


def insert_papers(query: str, papers: Iterable[PaperCandidate]) -> list[int]:
    ids: list[int] = []
    with connect() as conn:
        for paper in papers:
            cursor = conn.execute(
                """
                INSERT INTO papers (
                    query, title, authors, summary, published, source, publisher, doi,
                    page_url, pdf_url, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query,
                    paper.title,
                    paper.authors,
                    paper.summary,
                    paper.published,
                    paper.source,
                    paper.publisher,
                    paper.doi,
                    paper.page_url,
                    paper.pdf_url,
                    paper.status,
                ),
            )
            ids.append(int(cursor.lastrowid))
    return ids


def list_papers(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(
            conn.execute(
                "SELECT * FROM papers ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            )
        )


def get_paper(paper_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()


def update_download_status(
    paper_id: int,
    status: str,
    *,
    local_path: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE papers
            SET status = ?, local_path = COALESCE(?, local_path), error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, local_path, error, paper_id),
        )


def export_papers_csv() -> Path:
    path = EXPORTS_DIR / "papers.csv"
    rows = list_papers(limit=10_000)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "id",
                "query",
                "title",
                "authors",
                "published",
                "source",
                "publisher",
                "doi",
                "page_url",
                "pdf_url",
                "local_path",
                "status",
                "error",
                "created_at",
                "updated_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["query"],
                    row["title"],
                    row["authors"],
                    row["published"],
                    row["source"],
                    row["publisher"],
                    row["doi"],
                    row["page_url"],
                    row["pdf_url"],
                    row["local_path"],
                    row["status"],
                    row["error"],
                    row["created_at"],
                    row["updated_at"],
                ]
            )
    return path


def export_papers_json() -> Path:
    path = EXPORTS_DIR / "papers.json"
    rows = [dict(row) for row in list_papers(limit=10_000)]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

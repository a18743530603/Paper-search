import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .config import DATABASE_PATH, EXPORTS_DIR, ensure_runtime_dirs
from .schemas import (
    INDEX_NOT_STARTED,
    PARSE_NOT_STARTED,
    RAG_ANSWERING,
    PaperCandidate,
    PaperChunk,
)


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
    downloaded_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_papers_created_at ON papers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_papers_query ON papers(query);

CREATE TABLE IF NOT EXISTS paper_documents (
    paper_id INTEGER PRIMARY KEY,
    parse_status TEXT NOT NULL DEFAULT 'not_started',
    page_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    index_status TEXT NOT NULL DEFAULT 'not_indexed',
    index_error TEXT,
    vectorizer TEXT,
    indexed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS paper_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    vector_json TEXT,
    dense_vector_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    UNIQUE (paper_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_paper_chunks_paper_id ON paper_chunks(paper_id);

CREATE TABLE IF NOT EXISTS rag_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    evidence_json TEXT,
    model TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rag_queries_paper_id
ON rag_queries(paper_id, id DESC);
"""


def connect() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
        if "downloaded_at" not in columns:
            conn.execute("ALTER TABLE papers ADD COLUMN downloaded_at TEXT")
        document_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(paper_documents)").fetchall()
        }
        for name, definition in (
            ("index_status", "TEXT NOT NULL DEFAULT 'not_indexed'"),
            ("index_error", "TEXT"),
            ("vectorizer", "TEXT"),
            ("indexed_at", "TEXT"),
        ):
            if name not in document_columns:
                conn.execute(
                    f"ALTER TABLE paper_documents ADD COLUMN {name} {definition}"
                )
        chunk_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(paper_chunks)").fetchall()
        }
        if "vector_json" not in chunk_columns:
            conn.execute("ALTER TABLE paper_chunks ADD COLUMN vector_json TEXT")
        if "dense_vector_json" not in chunk_columns:
            conn.execute("ALTER TABLE paper_chunks ADD COLUMN dense_vector_json TEXT")
        conn.execute(
            """
            UPDATE papers
            SET downloaded_at = updated_at
            WHERE status = 'downloaded' AND downloaded_at IS NULL
            """
        )
        conn.execute(
            """
            UPDATE papers
            SET status = 'available'
            WHERE status = 'downloading'
              AND local_path IS NULL
              AND pdf_url IS NOT NULL
            """
        )


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


def clear_paper_history() -> int:
    with connect() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
        conn.execute("DELETE FROM papers")
    return count


def get_paper(paper_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()


def list_paper_statuses(limit: int = 1000) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.status,
                p.local_path,
                p.error,
                p.downloaded_at,
                p.updated_at,
                d.parse_status,
                d.page_count,
                d.chunk_count,
                d.error AS parse_error,
                d.index_status,
                d.index_error,
                d.indexed_at,
                (
                    SELECT q.status
                    FROM rag_queries AS q
                    WHERE q.paper_id = p.id
                    ORDER BY q.id DESC
                    LIMIT 1
                ) AS rag_status
            FROM papers AS p
            LEFT JOIN paper_documents AS d ON d.paper_id = p.id
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_document(paper_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM paper_documents WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()


def get_or_create_document(paper_id: int) -> sqlite3.Row:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_documents (paper_id, parse_status)
            VALUES (?, ?)
            """,
            (paper_id, PARSE_NOT_STARTED),
        )
        return conn.execute(
            "SELECT * FROM paper_documents WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()


def list_paper_chunks(paper_id: int, limit: int = 20) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM paper_chunks
                WHERE paper_id = ?
                ORDER BY chunk_index
                LIMIT ?
                """,
                (paper_id, limit),
            )
        )


def list_indexed_chunks(paper_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM paper_chunks
                WHERE paper_id = ? AND vector_json IS NOT NULL
                ORDER BY chunk_index
                """,
                (paper_id,),
            )
        )


def update_parse_status(
    paper_id: int,
    status: str,
    *,
    page_count: int = 0,
    chunk_count: int = 0,
    error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_documents (
                paper_id, parse_status, page_count, chunk_count, error
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                parse_status = excluded.parse_status,
                page_count = excluded.page_count,
                chunk_count = excluded.chunk_count,
                error = excluded.error,
                updated_at = CURRENT_TIMESTAMP
            """,
            (paper_id, status, page_count, chunk_count, error),
        )


def replace_paper_chunks(
    paper_id: int,
    chunks: Iterable[PaperChunk],
    *,
    page_count: int,
) -> int:
    chunk_list = list(chunks)
    with connect() as conn:
        conn.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
        conn.executemany(
            """
            INSERT INTO paper_chunks (paper_id, page_number, chunk_index, content)
            VALUES (?, ?, ?, ?)
            """,
            [
                (paper_id, chunk.page_number, chunk.chunk_index, chunk.content)
                for chunk in chunk_list
            ],
        )
        conn.execute(
            """
            INSERT INTO paper_documents (
                paper_id, parse_status, page_count, chunk_count, error,
                index_status, index_error, vectorizer, indexed_at
            )
            VALUES (?, 'parsed', ?, ?, NULL, 'not_indexed', NULL, NULL, NULL)
            ON CONFLICT(paper_id) DO UPDATE SET
                parse_status = excluded.parse_status,
                page_count = excluded.page_count,
                chunk_count = excluded.chunk_count,
                error = NULL,
                index_status = 'not_indexed',
                index_error = NULL,
                vectorizer = NULL,
                indexed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (paper_id, page_count, len(chunk_list)),
        )
    return len(chunk_list)


def update_index_status(
    paper_id: int,
    status: str,
    *,
    error: Optional[str] = None,
    vectorizer: Optional[str] = None,
) -> None:
    indexed_at = (
        datetime.now().astimezone().isoformat(timespec="seconds")
        if status == "indexed"
        else None
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_documents (
                paper_id, parse_status, index_status, index_error,
                vectorizer, indexed_at
            )
            VALUES (?, 'not_started', ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                index_status = excluded.index_status,
                index_error = excluded.index_error,
                vectorizer = COALESCE(excluded.vectorizer, vectorizer),
                indexed_at = COALESCE(excluded.indexed_at, indexed_at),
                updated_at = CURRENT_TIMESTAMP
            """,
            (paper_id, status, error, vectorizer, indexed_at),
        )


def replace_chunk_vectors(
    paper_id: int,
    vectors: dict[int, dict[str, float]],
    *,
    vectorizer: str,
    dense_vectors: Optional[dict[int, list[float]]] = None,
    warning: Optional[str] = None,
) -> None:
    indexed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            UPDATE paper_chunks
            SET vector_json = NULL, dense_vector_json = NULL
            WHERE paper_id = ?
            """,
            (paper_id,),
        )
        conn.executemany(
            """
            UPDATE paper_chunks
            SET vector_json = ?
            WHERE id = ? AND paper_id = ?
            """,
            [
                (json.dumps(vector, ensure_ascii=False), chunk_id, paper_id)
                for chunk_id, vector in vectors.items()
            ],
        )
        if dense_vectors:
            conn.executemany(
                """
                UPDATE paper_chunks
                SET dense_vector_json = ?
                WHERE id = ? AND paper_id = ?
                """,
                [
                    (json.dumps(vector), chunk_id, paper_id)
                    for chunk_id, vector in dense_vectors.items()
                ],
            )
        conn.execute(
            """
            UPDATE paper_documents
            SET index_status = 'indexed', index_error = ?,
                vectorizer = ?, indexed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE paper_id = ?
            """,
            (warning, vectorizer, indexed_at, paper_id),
        )


def create_rag_query(paper_id: int, question: str) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO rag_queries (paper_id, question, status)
            VALUES (?, ?, ?)
            """,
            (paper_id, question, RAG_ANSWERING),
        )
        return int(cursor.lastrowid)


def get_rag_query(query_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM rag_queries WHERE id = ?",
            (query_id,),
        ).fetchone()


def list_rag_queries(paper_id: int, limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rag_queries
            WHERE paper_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (paper_id, limit),
        ).fetchall()
    queries: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item["evidence_json"] or "[]")
        except json.JSONDecodeError:
            item["evidence"] = []
        queries.append(item)
    return queries


def update_rag_query(
    query_id: int,
    status: str,
    *,
    answer: Optional[str] = None,
    evidence: Optional[list[dict]] = None,
    model: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE rag_queries
            SET status = ?, answer = ?, evidence_json = ?, model = ?, error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                answer,
                json.dumps(evidence or [], ensure_ascii=False),
                model,
                error,
                query_id,
            ),
        )


def update_download_status(
    paper_id: int,
    status: str,
    *,
    local_path: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    downloaded_at = (
        datetime.now().astimezone().isoformat(timespec="seconds")
        if status == "downloaded"
        else None
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE papers
            SET status = ?, local_path = COALESCE(?, local_path), error = ?,
                downloaded_at = CASE
                    WHEN ? IS NOT NULL THEN ?
                    ELSE downloaded_at
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, local_path, error, downloaded_at, downloaded_at, paper_id),
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
                "downloaded_at",
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
                    row["downloaded_at"],
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

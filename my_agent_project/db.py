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
    RESEARCH_REPORT_RUNNING,
    STATUS_DOWNLOADING,
    PaperCandidate,
    PaperChunk,
    PaperParentChunk,
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

CREATE TABLE IF NOT EXISTS research_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    max_results INTEGER NOT NULL,
    plan_goal TEXT NOT NULL,
    plan_queries_json TEXT NOT NULL,
    plan_rationale TEXT NOT NULL DEFAULT '',
    plan_model TEXT NOT NULL DEFAULT '',
    planner_status TEXT NOT NULL,
    planner_error TEXT,
    checkpoint_thread_id TEXT,
    status TEXT NOT NULL,
    paper_ids_json TEXT NOT NULL DEFAULT '[]',
    result_count INTEGER NOT NULL DEFAULT 0,
    selected_paper_ids_json TEXT NOT NULL DEFAULT '[]',
    selected_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT,
    selected_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_research_tasks_created_at
ON research_tasks(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS research_tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    call_index INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    source_counts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE,
    UNIQUE (task_id, call_index)
);

CREATE INDEX IF NOT EXISTS idx_research_tool_calls_task_id
ON research_tool_calls(task_id, call_index);

CREATE TABLE IF NOT EXISTS research_reports (
    task_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'not_started',
    content TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    model TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS paper_documents (
    paper_id INTEGER PRIMARY KEY,
    parse_status TEXT NOT NULL DEFAULT 'not_started',
    page_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    source_hash TEXT,
    chunk_strategy TEXT,
    chunk_size INTEGER,
    chunk_overlap INTEGER,
    layout_stats_json TEXT,
    index_status TEXT NOT NULL DEFAULT 'not_indexed',
    index_error TEXT,
    vectorizer TEXT,
    embedding_namespace TEXT,
    indexed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS paper_parent_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    parent_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    UNIQUE (paper_id, parent_index)
);

CREATE INDEX IF NOT EXISTS idx_paper_parent_chunks_paper_id
ON paper_parent_chunks(paper_id);

CREATE TABLE IF NOT EXISTS paper_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    parent_chunk_id INTEGER,
    chunk_role TEXT NOT NULL DEFAULT 'retrieval',
    vector_json TEXT,
    dense_vector_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_chunk_id) REFERENCES paper_parent_chunks(id) ON DELETE CASCADE,
    UNIQUE (paper_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_paper_chunks_paper_id ON paper_chunks(paper_id);

CREATE TABLE IF NOT EXISTS pdf_text_cache (
    source_hash TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_hash, page_number)
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    cache_namespace TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cache_namespace, content_hash)
);

CREATE TABLE IF NOT EXISTS llm_response_cache (
    cache_namespace TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (cache_namespace, input_hash)
);

CREATE TABLE IF NOT EXISTS rag_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    reranker TEXT NOT NULL DEFAULT 'none',
    rerank_error TEXT,
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

CREATE TABLE IF NOT EXISTS evaluation_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    question_type TEXT NOT NULL,
    question TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    evidence_page INTEGER NOT NULL,
    evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    UNIQUE (paper_id, question)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_cases_paper_id
ON evaluation_cases(paper_id, id);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    chunk_strategy TEXT NOT NULL,
    query_mode TEXT NOT NULL DEFAULT 'original',
    keyword_retriever TEXT NOT NULL DEFAULT 'tfidf',
    reranker TEXT NOT NULL DEFAULT 'none',
    chunk_size INTEGER NOT NULL,
    chunk_overlap INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    semantic_weight REAL NOT NULL,
    keyword_weight REAL NOT NULL,
    top_k INTEGER NOT NULL,
    case_count INTEGER NOT NULL DEFAULT 0,
    metrics_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    case_id INTEGER NOT NULL,
    hit_rank INTEGER,
    reciprocal_rank REAL NOT NULL DEFAULT 0,
    best_coverage REAL NOT NULL DEFAULT 0,
    retrieved_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES evaluation_cases(id) ON DELETE CASCADE,
    UNIQUE (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_results_run_id
ON evaluation_results(run_id, case_id);

CREATE TABLE IF NOT EXISTS generation_evaluation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    retrieval_run_id INTEGER NOT NULL,
    generator_model TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    case_count INTEGER NOT NULL DEFAULT 0,
    metrics_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (retrieval_run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS generation_evaluation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    case_id INTEGER NOT NULL,
    answer TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    faithfulness REAL NOT NULL DEFAULT 0,
    answer_relevance REAL NOT NULL DEFAULT 0,
    reference_correctness REAL NOT NULL DEFAULT 0,
    citation_correctness REAL NOT NULL DEFAULT 0,
    judge_json TEXT NOT NULL DEFAULT '{}',
    generation_seconds REAL NOT NULL DEFAULT 0,
    judge_seconds REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES generation_evaluation_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES evaluation_cases(id) ON DELETE CASCADE,
    UNIQUE (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS idx_generation_evaluation_results_run_id
ON generation_evaluation_results(run_id, case_id);
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
            ("source_hash", "TEXT"),
            ("chunk_strategy", "TEXT"),
            ("chunk_size", "INTEGER"),
            ("chunk_overlap", "INTEGER"),
            ("layout_stats_json", "TEXT"),
            ("index_status", "TEXT NOT NULL DEFAULT 'not_indexed'"),
            ("index_error", "TEXT"),
            ("vectorizer", "TEXT"),
            ("embedding_namespace", "TEXT"),
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
        if "parent_chunk_id" not in chunk_columns:
            conn.execute("ALTER TABLE paper_chunks ADD COLUMN parent_chunk_id INTEGER")
        if "chunk_role" not in chunk_columns:
            conn.execute(
                "ALTER TABLE paper_chunks "
                "ADD COLUMN chunk_role TEXT NOT NULL DEFAULT 'retrieval'"
            )
        evaluation_run_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(evaluation_runs)").fetchall()
        }
        if "query_mode" not in evaluation_run_columns:
            conn.execute(
                "ALTER TABLE evaluation_runs "
                "ADD COLUMN query_mode TEXT NOT NULL DEFAULT 'original'"
            )
        if "keyword_retriever" not in evaluation_run_columns:
            conn.execute(
                "ALTER TABLE evaluation_runs "
                "ADD COLUMN keyword_retriever TEXT NOT NULL DEFAULT 'tfidf'"
            )
        if "reranker" not in evaluation_run_columns:
            conn.execute(
                "ALTER TABLE evaluation_runs "
                "ADD COLUMN reranker TEXT NOT NULL DEFAULT 'none'"
            )
        rag_query_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(rag_queries)").fetchall()
        }
        if "reranker" not in rag_query_columns:
            conn.execute(
                "ALTER TABLE rag_queries "
                "ADD COLUMN reranker TEXT NOT NULL DEFAULT 'none'"
            )
        if "rerank_error" not in rag_query_columns:
            conn.execute("ALTER TABLE rag_queries ADD COLUMN rerank_error TEXT")
        research_task_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_tasks)").fetchall()
        }
        for name, definition in (
            ("checkpoint_thread_id", "TEXT"),
            ("selected_paper_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("selected_count", "INTEGER NOT NULL DEFAULT 0"),
            ("selected_at", "TEXT"),
        ):
            if name not in research_task_columns:
                conn.execute(
                    f"ALTER TABLE research_tasks ADD COLUMN {name} {definition}"
                )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_research_tasks_checkpoint_thread
            ON research_tasks(checkpoint_thread_id)
            WHERE checkpoint_thread_id IS NOT NULL
            """
        )
        conn.execute(
            """
            UPDATE papers
            SET downloaded_at = updated_at
            WHERE status = 'downloaded' AND downloaded_at IS NULL
            """
        )
        conn.execute(
            """
            UPDATE research_tasks
            SET status = 'awaiting_confirmation',
                error = '上一次执行被应用重启中断，请重新确认。',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
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
        conn.execute(
            """
            UPDATE paper_documents
            SET parse_status = 'parse_failed',
                error = '上一次解析被应用重启中断，可以重新执行。',
                updated_at = CURRENT_TIMESTAMP
            WHERE parse_status = 'parsing'
            """
        )
        conn.execute(
            """
            UPDATE paper_documents
            SET index_status = 'index_failed',
                index_error = '上一次索引被应用重启中断，可以重新执行。',
                updated_at = CURRENT_TIMESTAMP
            WHERE index_status = 'indexing'
            """
        )
        conn.execute(
            """
            UPDATE research_reports
            SET status = 'failed',
                error = '上一次报告执行被应用重启中断，请点击重新生成以从检查点继续。',
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('preparing', 'retrieving', 'generating')
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


def _research_task_from_row(row: sqlite3.Row) -> dict:
    task = dict(row)
    for source, target in (
        ("plan_queries_json", "plan_queries"),
        ("paper_ids_json", "paper_ids"),
        ("selected_paper_ids_json", "selected_paper_ids"),
    ):
        try:
            value = json.loads(task[source] or "[]")
        except (json.JSONDecodeError, TypeError):
            value = []
        task[target] = value if isinstance(value, list) else []
    return task


def create_research_task(
    *,
    question: str,
    max_results: int,
    plan_goal: str,
    plan_queries: list[str],
    plan_rationale: str,
    plan_model: str,
    planner_status: str,
    planner_error: str,
    status: str,
    checkpoint_thread_id: str = "",
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_tasks (
                question, max_results, plan_goal, plan_queries_json,
                plan_rationale, plan_model, planner_status, planner_error,
                checkpoint_thread_id, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                max_results,
                plan_goal,
                json.dumps(plan_queries, ensure_ascii=False),
                plan_rationale,
                plan_model,
                planner_status,
                planner_error or None,
                checkpoint_thread_id or None,
                status,
            ),
        )
        return int(cursor.lastrowid)


def get_research_task(task_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return _research_task_from_row(row) if row is not None else None


def list_research_tasks(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_tasks
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_research_task_from_row(row) for row in rows]


def replace_research_tool_calls(
    task_id: int,
    tool_calls: list[dict],
) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM research_tool_calls WHERE task_id = ?", (task_id,))
        for call_index, call in enumerate(tool_calls, start=1):
            arguments = call.get("input")
            source_counts = call.get("source_counts")
            conn.execute(
                """
                INSERT INTO research_tool_calls (
                    task_id, call_index, tool_name, input_json, status,
                    result_count, source_counts_json, error, duration_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    call_index,
                    str(call.get("tool_name") or ""),
                    json.dumps(
                        arguments if isinstance(arguments, dict) else {},
                        ensure_ascii=False,
                    ),
                    str(call.get("status") or "failed"),
                    max(0, int(call.get("result_count") or 0)),
                    json.dumps(
                        source_counts if isinstance(source_counts, dict) else {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    str(call.get("error") or "")[:500] or None,
                    max(0, int(call.get("duration_ms") or 0)),
                ),
            )


def list_research_tool_calls(task_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_tool_calls
            WHERE task_id = ?
            ORDER BY call_index
            """,
            (task_id,),
        ).fetchall()
    calls: list[dict] = []
    for row in rows:
        call = dict(row)
        for source, target in (
            ("input_json", "input"),
            ("source_counts_json", "source_counts"),
        ):
            try:
                value = json.loads(call[source] or "{}")
            except (json.JSONDecodeError, TypeError):
                value = {}
            call[target] = value if isinstance(value, dict) else {}
        calls.append(call)
    return calls


def _research_report_from_row(row: sqlite3.Row) -> dict:
    report = dict(row)
    try:
        details = json.loads(report["details_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        details = {}
    report["details"] = details if isinstance(details, dict) else {}
    return report


def get_research_report(task_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM research_reports WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return _research_report_from_row(row) if row is not None else None


def start_research_report(task_id: int) -> bool:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT status, selected_count FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if (
            task is None
            or task["status"] != "papers_selected"
            or int(task["selected_count"]) < 1
        ):
            return False
        existing = conn.execute(
            "SELECT status FROM research_reports WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if existing is not None and existing["status"] in RESEARCH_REPORT_RUNNING:
            return False
        conn.execute(
            """
            INSERT INTO research_reports (
                task_id, status, content, details_json, model, error,
                started_at, completed_at
            )
            VALUES (?, 'preparing', NULL, '{}', NULL, NULL, CURRENT_TIMESTAMP, NULL)
            ON CONFLICT(task_id) DO UPDATE SET
                status = 'preparing', content = NULL, details_json = '{}',
                model = NULL, error = NULL, started_at = CURRENT_TIMESTAMP,
                completed_at = NULL, updated_at = CURRENT_TIMESTAMP
            """,
            (task_id,),
        )
        return True


def update_research_report(
    task_id: int,
    status: str,
    *,
    content: Optional[str] = None,
    details: Optional[dict] = None,
    model: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    details_json = (
        json.dumps(details, ensure_ascii=False) if details is not None else None
    )
    completed = status in {"completed", "failed"}
    with connect() as conn:
        conn.execute(
            """
            UPDATE research_reports
            SET status = ?,
                content = COALESCE(?, content),
                details_json = COALESCE(?, details_json),
                model = COALESCE(?, model),
                error = ?,
                completed_at = CASE
                    WHEN ? THEN CURRENT_TIMESTAMP
                    ELSE completed_at
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            (
                status,
                content,
                details_json,
                model,
                error[:1000] if error else None,
                int(completed),
                task_id,
            ),
        )


def start_research_task(task_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET status = 'running', error = NULL,
                confirmed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'awaiting_confirmation'
            """,
            (task_id,),
        )
        return cursor.rowcount == 1


def cancel_research_task(task_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('awaiting_confirmation', 'awaiting_selection')
            """,
            (task_id,),
        )
        return cursor.rowcount == 1


def save_research_task_results(task_id: int, paper_ids: list[int]) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET status = 'awaiting_selection', paper_ids_json = ?, result_count = ?,
                selected_paper_ids_json = '[]', selected_count = 0,
                selected_at = NULL, error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(paper_ids), len(paper_ids), task_id),
        )


def validate_research_task_paper_selection(
    task: dict,
    paper_ids: list[int],
    *,
    max_selected: int = 5,
) -> list[int]:
    selected_ids = list(dict.fromkeys(int(paper_id) for paper_id in paper_ids))
    if not selected_ids:
        raise ValueError("请至少选择一篇论文")
    if len(selected_ids) > max_selected:
        raise ValueError(f"最多选择{max_selected}篇论文")
    candidate_ids = {int(paper_id) for paper_id in task.get("paper_ids", [])}
    if any(paper_id not in candidate_ids for paper_id in selected_ids):
        raise ValueError("选择中包含不属于该任务的论文")
    return selected_ids


def select_research_task_papers(
    task_id: int,
    paper_ids: list[int],
    *,
    max_selected: int = 5,
) -> bool:
    task = get_research_task(task_id)
    if task is None or task["status"] != "awaiting_selection":
        return False
    selected_ids = validate_research_task_paper_selection(
        task,
        paper_ids,
        max_selected=max_selected,
    )

    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET status = 'papers_selected', selected_paper_ids_json = ?,
                selected_count = ?, selected_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'awaiting_selection'
            """,
            (json.dumps(selected_ids), len(selected_ids), task_id),
        )
        return cursor.rowcount == 1


def fail_research_task(task_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE research_tasks
            SET status = 'failed', error = ?, completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error[:1000], task_id),
        )


def clear_paper_history() -> int:
    with connect() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
        conn.execute("DELETE FROM evaluation_runs")
        conn.execute("DELETE FROM papers")
    return count


def register_local_paper(title: str, local_path: Path) -> int:
    resolved_path = local_path.resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"本地 PDF 不存在：{resolved_path}")
    downloaded_at = datetime.fromtimestamp(
        resolved_path.stat().st_mtime
    ).astimezone().isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM papers WHERE title = ? ORDER BY id LIMIT 1",
            (title,),
        ).fetchone()
        if existing is not None:
            paper_id = int(existing["id"])
            conn.execute(
                """
                UPDATE papers
                SET local_path = ?, status = 'downloaded', downloaded_at = ?,
                    error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(resolved_path), downloaded_at, paper_id),
            )
            return paper_id
        cursor = conn.execute(
            """
            INSERT INTO papers (
                query, title, authors, summary, published, source, publisher,
                doi, page_url, pdf_url, local_path, status, downloaded_at
            )
            VALUES ('local benchmark', ?, '', '', '', 'local', '', NULL, '',
                    NULL, ?, 'downloaded', ?)
            """,
            (title, str(resolved_path), downloaded_at),
        )
        return int(cursor.lastrowid)


def get_paper(paper_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()


def list_paper_statuses(limit: int = 1000) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.source,
                p.pdf_url,
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
                SELECT
                    c.*,
                    p.page_number AS parent_page_number,
                    p.parent_index,
                    p.content AS parent_content
                FROM paper_chunks AS c
                LEFT JOIN paper_parent_chunks AS p ON p.id = c.parent_chunk_id
                WHERE c.paper_id = ? AND c.vector_json IS NOT NULL
                ORDER BY c.chunk_index
                """,
                (paper_id,),
            )
        )


def count_paper_parent_chunks(paper_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM paper_parent_chunks WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
    return int(row[0])


def get_cached_pdf_pages(source_hash: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT content FROM pdf_text_cache
            WHERE source_hash = ?
            ORDER BY page_number
            """,
            (source_hash,),
        ).fetchall()
    return [str(row["content"]) for row in rows]


def replace_cached_pdf_pages(source_hash: str, pages: Iterable[str]) -> int:
    page_list = list(pages)
    with connect() as conn:
        conn.execute("DELETE FROM pdf_text_cache WHERE source_hash = ?", (source_hash,))
        conn.executemany(
            """
            INSERT INTO pdf_text_cache (source_hash, page_number, content)
            VALUES (?, ?, ?)
            """,
            [
                (source_hash, page_number, content)
                for page_number, content in enumerate(page_list, start=1)
            ],
        )
    return len(page_list)


def get_cached_embeddings(
    cache_namespace: str,
    content_hashes: Iterable[str],
) -> dict[str, list[float]]:
    hashes = list(dict.fromkeys(content_hashes))
    if not hashes:
        return {}
    placeholders = ", ".join("?" for _ in hashes)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT content_hash, vector_json FROM embedding_cache
            WHERE cache_namespace = ? AND content_hash IN ({placeholders})
            """,
            (cache_namespace, *hashes),
        ).fetchall()
    cached: dict[str, list[float]] = {}
    for row in rows:
        try:
            vector = json.loads(row["vector_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(vector, list) and vector:
            cached[str(row["content_hash"])] = vector
    return cached


def upsert_cached_embeddings(
    cache_namespace: str,
    vectors: dict[str, list[float]],
) -> int:
    if not vectors:
        return 0
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO embedding_cache (
                cache_namespace, content_hash, vector_json
            )
            VALUES (?, ?, ?)
            ON CONFLICT(cache_namespace, content_hash) DO UPDATE SET
                vector_json = excluded.vector_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                (cache_namespace, content_hash, json.dumps(vector))
                for content_hash, vector in vectors.items()
            ],
        )
    return len(vectors)


def get_cached_llm_response(
    cache_namespace: str,
    input_hash: str,
) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT response_json FROM llm_response_cache
            WHERE cache_namespace = ? AND input_hash = ?
            """,
            (cache_namespace, input_hash),
        ).fetchone()
    if row is None:
        return None
    try:
        response = json.loads(row["response_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return response if isinstance(response, dict) else None


def upsert_cached_llm_response(
    cache_namespace: str,
    input_hash: str,
    response: dict,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO llm_response_cache (
                cache_namespace, input_hash, response_json
            )
            VALUES (?, ?, ?)
            ON CONFLICT(cache_namespace, input_hash) DO UPDATE SET
                response_json = excluded.response_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                cache_namespace,
                input_hash,
                json.dumps(response, ensure_ascii=False),
            ),
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
    parent_chunks: Iterable[PaperParentChunk] = (),
    source_hash: Optional[str] = None,
    chunk_strategy: Optional[str] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    layout_stats: Optional[dict] = None,
) -> int:
    chunk_list = list(chunks)
    parent_list = list(parent_chunks)
    with connect() as conn:
        conn.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM paper_parent_chunks WHERE paper_id = ?", (paper_id,))
        conn.executemany(
            """
            INSERT INTO paper_parent_chunks (
                paper_id, page_number, parent_index, content
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (paper_id, parent.page_number, parent.parent_index, parent.content)
                for parent in parent_list
            ],
        )
        parent_ids = {
            int(row["parent_index"]): int(row["id"])
            for row in conn.execute(
                """
                SELECT id, parent_index FROM paper_parent_chunks
                WHERE paper_id = ?
                """,
                (paper_id,),
            )
        }
        missing_parent_indexes = {
            chunk.parent_index
            for chunk in chunk_list
            if chunk.parent_index is not None and chunk.parent_index not in parent_ids
        }
        if missing_parent_indexes:
            raise ValueError(
                f"missing parent chunks: {sorted(missing_parent_indexes)}"
            )
        conn.executemany(
            """
            INSERT INTO paper_chunks (
                paper_id, page_number, chunk_index, content, parent_chunk_id,
                chunk_role
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    paper_id,
                    chunk.page_number,
                    chunk.chunk_index,
                    chunk.content,
                    parent_ids.get(chunk.parent_index),
                    chunk.role,
                )
                for chunk in chunk_list
            ],
        )
        conn.execute(
            """
            INSERT INTO paper_documents (
                paper_id, parse_status, page_count, chunk_count, error,
                source_hash, chunk_strategy, chunk_size, chunk_overlap,
                layout_stats_json,
                index_status, index_error, vectorizer, embedding_namespace,
                indexed_at
            )
            VALUES (
                ?, 'parsed', ?, ?, NULL, ?, ?, ?, ?,
                ?,
                'not_indexed', NULL, NULL, NULL, NULL
            )
            ON CONFLICT(paper_id) DO UPDATE SET
                parse_status = excluded.parse_status,
                page_count = excluded.page_count,
                chunk_count = excluded.chunk_count,
                error = NULL,
                source_hash = excluded.source_hash,
                chunk_strategy = excluded.chunk_strategy,
                chunk_size = excluded.chunk_size,
                chunk_overlap = excluded.chunk_overlap,
                layout_stats_json = excluded.layout_stats_json,
                index_status = 'not_indexed',
                index_error = NULL,
                vectorizer = NULL,
                embedding_namespace = NULL,
                indexed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                paper_id,
                page_count,
                len(chunk_list),
                source_hash,
                chunk_strategy,
                chunk_size,
                chunk_overlap,
                json.dumps(layout_stats or {}, ensure_ascii=False),
            ),
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
    embedding_namespace: Optional[str] = None,
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
                vectorizer = ?, embedding_namespace = ?, indexed_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE paper_id = ?
            """,
            (warning, vectorizer, embedding_namespace, indexed_at, paper_id),
        )


def create_rag_query(
    paper_id: int,
    question: str,
    reranker: str = "none",
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO rag_queries (paper_id, question, reranker, status)
            VALUES (?, ?, ?, ?)
            """,
            (paper_id, question, reranker, RAG_ANSWERING),
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
    rerank_error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE rag_queries
            SET status = ?, answer = ?, evidence_json = ?, model = ?, error = ?,
                rerank_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                answer,
                json.dumps(evidence or [], ensure_ascii=False),
                model,
                error,
                rerank_error,
                query_id,
            ),
        )


def upsert_evaluation_case(
    paper_id: int,
    question_type: str,
    question: str,
    reference_answer: str,
    evidence_page: int,
    evidence_text: str,
) -> int:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO evaluation_cases (
                paper_id, question_type, question, reference_answer,
                evidence_page, evidence_text
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id, question) DO UPDATE SET
                question_type = excluded.question_type,
                reference_answer = excluded.reference_answer,
                evidence_page = excluded.evidence_page,
                evidence_text = excluded.evidence_text,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                paper_id,
                question_type,
                question,
                reference_answer,
                evidence_page,
                evidence_text,
            ),
        )
        row = conn.execute(
            """
            SELECT id FROM evaluation_cases
            WHERE paper_id = ? AND question = ?
            """,
            (paper_id, question),
        ).fetchone()
        return int(row["id"])


def list_evaluation_cases() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, p.title AS paper_title, p.local_path,
                   d.parse_status, d.index_status, d.vectorizer
            FROM evaluation_cases AS c
            JOIN papers AS p ON p.id = c.paper_id
            LEFT JOIN paper_documents AS d ON d.paper_id = p.id
            ORDER BY p.id, c.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_evaluation_run(
    *,
    name: str,
    chunk_strategy: str,
    query_mode: str,
    keyword_retriever: str,
    chunk_size: int,
    chunk_overlap: int,
    embedding_model: str,
    semantic_weight: float,
    keyword_weight: float,
    top_k: int,
    case_count: int,
    reranker: str = "none",
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO evaluation_runs (
                name, status, chunk_strategy, query_mode, keyword_retriever, reranker,
                chunk_size, chunk_overlap, embedding_model, semantic_weight,
                keyword_weight, top_k, case_count
            )
            VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                chunk_strategy,
                query_mode,
                keyword_retriever,
                reranker,
                chunk_size,
                chunk_overlap,
                embedding_model,
                semantic_weight,
                keyword_weight,
                top_k,
                case_count,
            ),
        )
        return int(cursor.lastrowid)


def save_evaluation_result(
    run_id: int,
    case_id: int,
    *,
    hit_rank: Optional[int],
    reciprocal_rank: float,
    best_coverage: float,
    retrieved: list[dict],
    error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO evaluation_results (
                run_id, case_id, hit_rank, reciprocal_rank, best_coverage,
                retrieved_json, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, case_id) DO UPDATE SET
                hit_rank = excluded.hit_rank,
                reciprocal_rank = excluded.reciprocal_rank,
                best_coverage = excluded.best_coverage,
                retrieved_json = excluded.retrieved_json,
                error = excluded.error
            """,
            (
                run_id,
                case_id,
                hit_rank,
                reciprocal_rank,
                best_coverage,
                json.dumps(retrieved, ensure_ascii=False),
                error,
            ),
        )


def finish_evaluation_run(
    run_id: int,
    status: str,
    *,
    metrics: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            UPDATE evaluation_runs
            SET status = ?, metrics_json = ?, error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(metrics or {}, ensure_ascii=False),
                error,
                completed_at,
                run_id,
            ),
        )


def list_evaluation_runs(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM evaluation_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    runs: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["metrics"] = json.loads(item["metrics_json"] or "{}")
        except json.JSONDecodeError:
            item["metrics"] = {}
        runs.append(item)
    return runs


def get_evaluation_run(run_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM evaluation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["metrics"] = json.loads(item["metrics_json"] or "{}")
    except json.JSONDecodeError:
        item["metrics"] = {}
    return item


def list_evaluation_results(run_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.paper_id, c.question_type, c.question, c.reference_answer,
                   c.evidence_page, c.evidence_text, p.title AS paper_title
            FROM evaluation_results AS r
            JOIN evaluation_cases AS c ON c.id = r.case_id
            JOIN papers AS p ON p.id = c.paper_id
            WHERE r.run_id = ?
            ORDER BY c.paper_id, c.id
            """,
            (run_id,),
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["retrieved"] = json.loads(item["retrieved_json"] or "[]")
        except json.JSONDecodeError:
            item["retrieved"] = []
        results.append(item)
    return results


def create_generation_evaluation_run(
    *,
    name: str,
    retrieval_run_id: int,
    generator_model: str,
    judge_model: str,
    prompt_version: str,
    case_count: int,
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO generation_evaluation_runs (
                name, status, retrieval_run_id, generator_model,
                judge_model, prompt_version, case_count
            )
            VALUES (?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                name,
                retrieval_run_id,
                generator_model,
                judge_model,
                prompt_version,
                case_count,
            ),
        )
        return int(cursor.lastrowid)


def save_generation_evaluation_result(
    run_id: int,
    case_id: int,
    *,
    answer: Optional[str],
    evidence: list[dict],
    faithfulness: float,
    answer_relevance: float,
    reference_correctness: float,
    citation_correctness: float,
    judge: dict,
    generation_seconds: float,
    judge_seconds: float,
    error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO generation_evaluation_results (
                run_id, case_id, answer, evidence_json, faithfulness,
                answer_relevance, reference_correctness,
                citation_correctness, judge_json, generation_seconds,
                judge_seconds, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, case_id) DO UPDATE SET
                answer = excluded.answer,
                evidence_json = excluded.evidence_json,
                faithfulness = excluded.faithfulness,
                answer_relevance = excluded.answer_relevance,
                reference_correctness = excluded.reference_correctness,
                citation_correctness = excluded.citation_correctness,
                judge_json = excluded.judge_json,
                generation_seconds = excluded.generation_seconds,
                judge_seconds = excluded.judge_seconds,
                error = excluded.error
            """,
            (
                run_id,
                case_id,
                answer,
                json.dumps(evidence, ensure_ascii=False),
                faithfulness,
                answer_relevance,
                reference_correctness,
                citation_correctness,
                json.dumps(judge, ensure_ascii=False),
                generation_seconds,
                judge_seconds,
                error,
            ),
        )


def finish_generation_evaluation_run(
    run_id: int,
    status: str,
    *,
    metrics: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            UPDATE generation_evaluation_runs
            SET status = ?, metrics_json = ?, error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(metrics or {}, ensure_ascii=False),
                error,
                completed_at,
                run_id,
            ),
        )


def _generation_run_from_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    try:
        item["metrics"] = json.loads(item["metrics_json"] or "{}")
    except json.JSONDecodeError:
        item["metrics"] = {}
    return item


def list_generation_evaluation_runs(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT g.*, e.name AS retrieval_run_name
            FROM generation_evaluation_runs AS g
            JOIN evaluation_runs AS e ON e.id = g.retrieval_run_id
            ORDER BY g.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_generation_run_from_row(row) for row in rows]


def get_generation_evaluation_run(run_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT g.*, e.name AS retrieval_run_name
            FROM generation_evaluation_runs AS g
            JOIN evaluation_runs AS e ON e.id = g.retrieval_run_id
            WHERE g.id = ?
            """,
            (run_id,),
        ).fetchone()
    return _generation_run_from_row(row) if row is not None else None


def list_generation_evaluation_results(run_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.question_type, c.question, c.reference_answer,
                   c.evidence_page, p.title AS paper_title
            FROM generation_evaluation_results AS r
            JOIN evaluation_cases AS c ON c.id = r.case_id
            JOIN papers AS p ON p.id = c.paper_id
            WHERE r.run_id = ?
            ORDER BY c.paper_id, c.id
            """,
            (run_id,),
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        for source, target, fallback in (
            ("evidence_json", "evidence", []),
            ("judge_json", "judge", {}),
        ):
            try:
                item[target] = json.loads(item[source] or json.dumps(fallback))
            except json.JSONDecodeError:
                item[target] = fallback
        results.append(item)
    return results


def claim_download(paper_id: int, allowed_statuses: Iterable[str]) -> bool:
    statuses = tuple(dict.fromkeys(allowed_statuses))
    if not statuses:
        return False

    placeholders = ", ".join("?" for _ in statuses)
    with connect() as conn:
        cursor = conn.execute(
            f"""
            UPDATE papers
            SET status = ?, error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ({placeholders})
            """,
            (STATUS_DOWNLOADING, paper_id, *statuses),
        )
    return cursor.rowcount == 1


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

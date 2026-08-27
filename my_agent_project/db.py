import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .config import DATABASE_PATH, EXPORTS_DIR, ensure_runtime_dirs
from .schemas import (
    INDEX_INDEXED,
    INDEX_INDEXING,
    INDEX_NOT_STARTED,
    PARSE_PARSED,
    PARSE_PARSING,
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

CREATE TABLE IF NOT EXISTS background_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL,
    paper_id INTEGER,
    rag_query_id INTEGER,
    research_task_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TEXT NOT NULL,
    lease_expires_at TEXT,
    worker_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (rag_query_id) REFERENCES rag_queries(id) ON DELETE CASCADE,
    FOREIGN KEY (research_task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_ready
ON background_jobs(status, available_at, priority DESC, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_active_dedupe
ON background_jobs(dedupe_key)
WHERE status IN ('queued', 'running', 'retry_wait');

CREATE TABLE IF NOT EXISTS research_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    max_results INTEGER NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '["arxiv", "crossref"]',
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
    report_question TEXT NOT NULL DEFAULT '',
    conversation_summary TEXT NOT NULL DEFAULT '',
    summary_up_to_message_id INTEGER NOT NULL DEFAULT 0,
    conversation_summary_error TEXT,
    conversation_summary_updated_at TEXT,
    task_memory_error TEXT,
    task_memory_updated_at TEXT,
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

CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    thread_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    status TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms INTEGER,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    model_calls INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_task_id
ON agent_traces(task_id, id);

CREATE INDEX IF NOT EXISTS idx_agent_traces_thread_node
ON agent_traces(thread_id, node_name, id);

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

CREATE TABLE IF NOT EXISTS research_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    phase TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_research_messages_task_id
ON research_messages(task_id, id);

CREATE TABLE IF NOT EXISTS research_task_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_task_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_from_message_id INTEGER NOT NULL,
    updated_from_message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (research_task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_research_task_memories_task_status
ON research_task_memories(research_task_id, status, id);

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
    parser_backend TEXT,
    parser_model TEXT,
    parser_job_id TEXT,
    parse_stage TEXT,
    parsed_at TEXT,
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
    section_title TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'text',
    bbox_json TEXT,
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _db_timestamp(value: Optional[datetime] = None) -> str:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
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
        background_job_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(background_jobs)").fetchall()
        }
        if "rag_query_id" not in background_job_columns:
            conn.execute("ALTER TABLE background_jobs ADD COLUMN rag_query_id INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_background_jobs_rag_query
            ON background_jobs(rag_query_id, id DESC)
            """
        )
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
            ("parser_backend", "TEXT"),
            ("parser_model", "TEXT"),
            ("parser_job_id", "TEXT"),
            ("parse_stage", "TEXT"),
            ("parsed_at", "TEXT"),
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
        if "section_title" not in chunk_columns:
            conn.execute(
                "ALTER TABLE paper_chunks "
                "ADD COLUMN section_title TEXT NOT NULL DEFAULT ''"
            )
        if "content_type" not in chunk_columns:
            conn.execute(
                "ALTER TABLE paper_chunks "
                "ADD COLUMN content_type TEXT NOT NULL DEFAULT 'text'"
            )
        if "bbox_json" not in chunk_columns:
            conn.execute("ALTER TABLE paper_chunks ADD COLUMN bbox_json TEXT")
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
            (
                "sources_json",
                "TEXT NOT NULL DEFAULT '[\"arxiv\", \"crossref\"]'",
            ),
            ("selected_paper_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("selected_count", "INTEGER NOT NULL DEFAULT 0"),
            ("selected_at", "TEXT"),
            ("report_question", "TEXT NOT NULL DEFAULT ''"),
            ("conversation_summary", "TEXT NOT NULL DEFAULT ''"),
            ("summary_up_to_message_id", "INTEGER NOT NULL DEFAULT 0"),
            ("conversation_summary_error", "TEXT"),
            ("conversation_summary_updated_at", "TEXT"),
            ("task_memory_error", "TEXT"),
            ("task_memory_updated_at", "TEXT"),
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
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.research_task_id = research_tasks.id
                    AND job.job_type = 'research_search'
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
            """
        )
        conn.execute(
            """
            UPDATE papers
            SET status = 'available'
            WHERE status = 'downloading'
              AND local_path IS NULL
              AND pdf_url IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.paper_id = papers.id
                    AND job.job_type IN ('download_paper', 'prepare_paper')
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
            """
        )
        conn.execute(
            """
            UPDATE paper_documents
            SET parse_status = 'parse_failed',
                error = '上一次解析被应用重启中断，可以重新执行。',
                updated_at = CURRENT_TIMESTAMP
            WHERE parse_status = 'parsing'
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.paper_id = paper_documents.paper_id
                    AND job.job_type IN ('prepare_paper', 'parse_paper')
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
            """
        )
        conn.execute(
            """
            UPDATE rag_queries
            SET status = 'answer_failed',
                error = '上一次问答被应用重启中断，可以重新提交。',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'answering'
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.rag_query_id = rag_queries.id
                    AND job.job_type = 'answer_rag_query'
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
            """
        )
        conn.execute(
            """
            UPDATE paper_documents
            SET index_status = 'index_failed',
                index_error = '上一次索引被应用重启中断，可以重新执行。',
                updated_at = CURRENT_TIMESTAMP
            WHERE index_status = 'indexing'
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.paper_id = paper_documents.paper_id
                    AND job.job_type IN ('prepare_paper', 'index_paper')
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
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
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.research_task_id = research_reports.task_id
                    AND job.job_type = 'research_report'
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
            """
        )
        conn.execute(
            """
            UPDATE research_messages
            SET status = 'failed',
                error = '上一次追问执行被应用重启中断，请重新提交。',
                updated_at = CURRENT_TIMESTAMP
            WHERE role = 'assistant'
              AND phase = 'followup'
              AND status IN ('pending', 'retry_wait')
              AND NOT EXISTS (
                  SELECT 1
                  FROM background_jobs AS job
                  WHERE job.research_task_id = research_messages.task_id
                    AND job.job_type = 'research_followup'
                    AND job.status IN ('queued', 'running', 'retry_wait')
              )
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
        ("sources_json", "sources"),
        ("plan_queries_json", "plan_queries"),
        ("paper_ids_json", "paper_ids"),
        ("selected_paper_ids_json", "selected_paper_ids"),
    ):
        try:
            value = json.loads(task[source] or "[]")
        except (json.JSONDecodeError, TypeError):
            value = []
        task[target] = value if isinstance(value, list) else []
    if not task.get("sources"):
        task["sources"] = ["arxiv", "crossref"]
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
    sources: Iterable[str] = ("arxiv", "crossref"),
) -> int:
    selected_sources = list(dict.fromkeys(str(source) for source in sources))
    if not selected_sources:
        selected_sources = ["arxiv", "crossref"]
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_tasks (
                question, max_results, sources_json, plan_goal, plan_queries_json,
                plan_rationale, plan_model, planner_status, planner_error,
                checkpoint_thread_id, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                max_results,
                json.dumps(selected_sources, ensure_ascii=False),
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
        task_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'discovery', ?)
            """,
            (task_id, question),
        )
        plan_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(plan_queries, 1))
        plan_message = f"研究目标：{plan_goal}\n\n检索计划：\n{plan_lines}"
        if plan_rationale:
            plan_message += f"\n\n规划说明：{plan_rationale}"
        conn.execute(
            """
            INSERT INTO research_messages (
                task_id, role, phase, content, model, error
            )
            VALUES (?, 'assistant', 'planning', ?, ?, ?)
            """,
            (
                task_id,
                plan_message,
                plan_model or None,
                planner_error or None,
            ),
        )
        return task_id


def revise_research_task_plan(
    task_id: int,
    *,
    question: str,
    plan_goal: str,
    plan_queries: list[str],
    plan_rationale: str,
    plan_model: str,
    planner_status: str,
    planner_error: str,
    user_message: str,
) -> bool:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET question = ?, plan_goal = ?, plan_queries_json = ?,
                plan_rationale = ?, plan_model = ?, planner_status = ?,
                planner_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'awaiting_confirmation'
            """,
            (
                question,
                plan_goal,
                json.dumps(plan_queries, ensure_ascii=False),
                plan_rationale,
                plan_model,
                planner_status,
                planner_error or None,
                task_id,
            ),
        )
        if cursor.rowcount != 1:
            return False
        conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'planning', ?)
            """,
            (task_id, user_message),
        )
        plan_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(plan_queries, 1))
        assistant_message = f"已更新研究目标：{plan_goal}\n\n新的检索计划：\n{plan_lines}"
        if plan_rationale:
            assistant_message += f"\n\n规划说明：{plan_rationale}"
        conn.execute(
            """
            INSERT INTO research_messages (
                task_id, role, phase, content, model, error
            )
            VALUES (?, 'assistant', 'planning', ?, ?, ?)
            """,
            (
                task_id,
                assistant_message,
                plan_model or None,
                planner_error or None,
            ),
        )
        return True


def set_research_report_question(
    task_id: int,
    question: str,
) -> bool:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET report_question = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'papers_selected'
            """,
            (question, task_id),
        )
        if cursor.rowcount != 1:
            return False
        conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'report_question', ?)
            """,
            (task_id, question),
        )
        conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (
                ?, 'assistant', 'report_question',
                '已记录报告问题。确认后我会准备所选论文、检索页码证据并生成报告。'
            )
            """,
            (task_id,),
        )
        return True


def get_research_task(task_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT task.*,
                   (
                       SELECT job.status
                       FROM background_jobs AS job
                       WHERE job.research_task_id = task.id
                         AND job.job_type = 'research_search'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS search_job_status,
                   (
                       SELECT job.attempt_count
                       FROM background_jobs AS job
                       WHERE job.research_task_id = task.id
                         AND job.job_type = 'research_search'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS search_job_attempts,
                   (
                       SELECT job.max_attempts
                       FROM background_jobs AS job
                       WHERE job.research_task_id = task.id
                         AND job.job_type = 'research_search'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS search_job_max_attempts
            FROM research_tasks AS task
            WHERE task.id = ?
            """,
            (task_id,),
        ).fetchone()
    return _research_task_from_row(row) if row is not None else None


def list_research_tasks(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT task.*,
                   (
                       SELECT job.status
                       FROM background_jobs AS job
                       WHERE job.research_task_id = task.id
                         AND job.job_type = 'research_search'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS search_job_status,
                   (
                       SELECT job.attempt_count
                       FROM background_jobs AS job
                       WHERE job.research_task_id = task.id
                         AND job.job_type = 'research_search'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS search_job_attempts,
                   (
                       SELECT job.max_attempts
                       FROM background_jobs AS job
                       WHERE job.research_task_id = task.id
                         AND job.job_type = 'research_search'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS search_job_max_attempts
            FROM research_tasks AS task
            ORDER BY task.created_at DESC, task.id DESC
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


def create_agent_trace(
    *,
    task_id: Optional[int],
    thread_id: str,
    node_name: str,
    start_time: str,
) -> int:
    thread_id = thread_id.strip()
    node_name = node_name.strip()
    if not thread_id or not node_name:
        raise ValueError("Agent Trace requires thread_id and node_name")

    with connect() as conn:
        retry_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM agent_traces
                WHERE thread_id = ? AND node_name = ?
                """,
                (thread_id, node_name),
            ).fetchone()[0]
        )
        cursor = conn.execute(
            """
            INSERT INTO agent_traces (
                task_id, thread_id, node_name, status, start_time, retry_count
            )
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            (task_id, thread_id, node_name, start_time, retry_count),
        )
        return int(cursor.lastrowid)


def finish_agent_trace(
    trace_id: int,
    *,
    status: str,
    end_time: str,
    duration_ms: int,
    tool_calls: Optional[list[dict]] = None,
    model_calls: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE agent_traces
            SET status = ?, end_time = ?, duration_ms = ?, tool_calls_json = ?,
                model_calls = ?, input_tokens = ?, output_tokens = ?, error = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                status,
                end_time,
                max(0, int(duration_ms)),
                json.dumps(tool_calls or [], ensure_ascii=False),
                model_calls,
                input_tokens,
                output_tokens,
                str(error or "")[:1000] or None,
                trace_id,
            ),
        )


def attach_agent_traces_to_task(thread_id: str, task_id: int) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE agent_traces
            SET task_id = ?
            WHERE thread_id = ? AND task_id IS NULL
            """,
            (task_id, thread_id),
        )
        return max(0, int(cursor.rowcount))


def list_agent_traces(
    *,
    task_id: Optional[int] = None,
    thread_id: str = "",
) -> list[dict]:
    if task_id is None and not thread_id:
        return []
    if task_id is not None:
        where_clause = "task_id = ?"
        parameters: tuple[object, ...] = (task_id,)
    else:
        where_clause = "thread_id = ?"
        parameters = (thread_id,)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM agent_traces
            WHERE {where_clause}
            ORDER BY id
            """,
            parameters,
        ).fetchall()
    return [_agent_trace_from_row(row) for row in rows]


def _agent_trace_from_row(row: sqlite3.Row) -> dict:
    trace = dict(row)
    try:
        tool_calls = json.loads(trace["tool_calls_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        tool_calls = []
    trace["tool_calls"] = tool_calls if isinstance(tool_calls, list) else []
    return trace


def list_agent_metric_traces() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_traces ORDER BY id"
        ).fetchall()
    return [_agent_trace_from_row(row) for row in rows]


def list_agent_metric_tasks() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.checkpoint_thread_id, t.planner_status,
                   t.status AS task_status, t.created_at, t.confirmed_at,
                   t.completed_at AS task_completed_at,
                   r.status AS report_status,
                   r.started_at AS report_started_at,
                   r.completed_at AS report_completed_at
            FROM research_tasks AS t
            LEFT JOIN research_reports AS r ON r.task_id = t.id
            ORDER BY t.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


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
            """
            SELECT report.*,
                   (
                       SELECT job.status
                       FROM background_jobs AS job
                       WHERE job.research_task_id = report.task_id
                         AND job.job_type = 'research_report'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS job_status,
                   (
                       SELECT job.attempt_count
                       FROM background_jobs AS job
                       WHERE job.research_task_id = report.task_id
                         AND job.job_type = 'research_report'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS job_attempts,
                   (
                       SELECT job.max_attempts
                       FROM background_jobs AS job
                       WHERE job.research_task_id = report.task_id
                         AND job.job_type = 'research_report'
                       ORDER BY job.id DESC
                       LIMIT 1
                   ) AS job_max_attempts
            FROM research_reports AS report
            WHERE report.task_id = ?
            """,
            (task_id,),
        ).fetchone()
    return _research_report_from_row(row) if row is not None else None


def _research_message_from_row(row: sqlite3.Row) -> dict:
    message = dict(row)
    try:
        evidence = json.loads(message.get("evidence_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        evidence = []
    message["evidence"] = evidence if isinstance(evidence, list) else []
    return message


def get_research_message(message_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM research_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    return _research_message_from_row(row) if row is not None else None


def list_research_messages(task_id: int, limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_messages
            WHERE task_id = ?
            ORDER BY id
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
    return [_research_message_from_row(row) for row in rows]


def list_recent_research_conversation_messages(
    task_id: int,
    *,
    limit: int = 10,
    exclude_message_ids: Iterable[int] = (),
) -> list[dict]:
    if limit < 1:
        return []

    excluded_ids = list(
        dict.fromkeys(
            message_id
            for value in exclude_message_ids
            if (message_id := int(value)) > 0
        )
    )
    exclusion_sql = ""
    parameters: list[object] = [task_id]
    if excluded_ids:
        placeholders = ", ".join("?" for _ in excluded_ids)
        exclusion_sql = f"AND id NOT IN ({placeholders})"
        parameters.extend(excluded_ids)
    parameters.append(limit)

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM (
                SELECT *
                FROM research_messages
                WHERE task_id = ?
                  AND phase = 'followup'
                  AND role IN ('user', 'assistant')
                  AND status = 'completed'
                  {exclusion_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            ) AS recent_messages
            ORDER BY created_at ASC, id ASC
            """,
            parameters,
        ).fetchall()
    return [_research_message_from_row(row) for row in rows]


def get_research_conversation_summary(task_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id AS task_id, conversation_summary,
                   summary_up_to_message_id, conversation_summary_error,
                   conversation_summary_updated_at
            FROM research_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_research_conversation_context_messages(
    task_id: int,
    *,
    recent_limit: int = 10,
    summary_up_to_message_id: int = 0,
    exclude_message_ids: Iterable[int] = (),
) -> dict[str, list[dict]]:
    if recent_limit < 1:
        raise ValueError("recent_limit must be at least 1")

    excluded_ids = list(
        dict.fromkeys(
            message_id
            for value in exclude_message_ids
            if (message_id := int(value)) > 0
        )
    )
    exclusion_sql = ""
    base_parameters: list[object] = [task_id]
    if excluded_ids:
        placeholders = ", ".join("?" for _ in excluded_ids)
        exclusion_sql = f"AND id NOT IN ({placeholders})"
        base_parameters.extend(excluded_ids)

    with connect() as conn:
        recent_rows = conn.execute(
            f"""
            SELECT *
            FROM research_messages
            WHERE task_id = ?
              AND phase = 'followup'
              AND role IN ('user', 'assistant')
              AND status = 'completed'
              {exclusion_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [*base_parameters, recent_limit],
        ).fetchall()
        recent_rows = list(reversed(recent_rows))

        messages_to_summarize: list[sqlite3.Row] = []
        if recent_rows:
            oldest_recent_id = int(recent_rows[0]["id"])
            messages_to_summarize = conn.execute(
                f"""
                SELECT *
                FROM research_messages
                WHERE task_id = ?
                  AND phase = 'followup'
                  AND role IN ('user', 'assistant')
                  AND status = 'completed'
                  {exclusion_sql}
                  AND id > ?
                  AND id < ?
                ORDER BY created_at ASC, id ASC
                """,
                [
                    *base_parameters,
                    max(0, int(summary_up_to_message_id)),
                    oldest_recent_id,
                ],
            ).fetchall()

    return {
        "messages_to_summarize": [
            _research_message_from_row(row) for row in messages_to_summarize
        ],
        "recent_messages": [
            _research_message_from_row(row) for row in recent_rows
        ],
    }


def update_research_conversation_summary(
    task_id: int,
    summary: str,
    summary_up_to_message_id: int,
    *,
    expected_previous_message_id: int,
) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET conversation_summary = ?,
                summary_up_to_message_id = ?,
                conversation_summary_error = NULL,
                conversation_summary_updated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND summary_up_to_message_id = ?
            """,
            (
                str(summary or "").strip(),
                max(0, int(summary_up_to_message_id)),
                task_id,
                max(0, int(expected_previous_message_id)),
            ),
        )
    return cursor.rowcount == 1


def record_research_conversation_summary_error(task_id: int, error: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET conversation_summary_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (str(error or "Conversation summary failed")[:1000], task_id),
        )
    return cursor.rowcount == 1


def list_research_task_memories(
    task_id: int,
    *,
    status: str = "active",
) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM research_task_memories
            WHERE research_task_id = ? AND status = ?
            ORDER BY id ASC
            """,
            (task_id, status),
        ).fetchall()
    return [dict(row) for row in rows]


def get_research_task_memory(memory_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM research_task_memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def apply_research_task_memory_operations(
    task_id: int,
    source_message_id: int,
    operations: Sequence[Mapping[str, object]],
) -> list[dict]:
    applied: list[dict] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT id FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise LookupError("研究任务不存在")
        source_message = conn.execute(
            """
            SELECT id
            FROM research_messages
            WHERE id = ? AND task_id = ? AND role = 'user' AND phase = 'followup'
            """,
            (source_message_id, task_id),
        ).fetchone()
        if source_message is None:
            raise ValueError("Task Memory来源消息无效")

        for operation in operations:
            action = str(operation["action"])
            if action == "add":
                category = str(operation["category"])
                content = str(operation["content"])
                duplicate = conn.execute(
                    """
                    SELECT id
                    FROM research_task_memories
                    WHERE research_task_id = ? AND status = 'active'
                      AND category = ? AND content = ?
                    LIMIT 1
                    """,
                    (task_id, category, content),
                ).fetchone()
                if duplicate is not None:
                    applied.append(
                        {
                            "action": "noop_duplicate",
                            "memory_id": int(duplicate["id"]),
                        }
                    )
                    continue
                cursor = conn.execute(
                    """
                    INSERT INTO research_task_memories (
                        research_task_id, category, content, status,
                        created_from_message_id, updated_from_message_id
                    )
                    VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        task_id,
                        category,
                        content,
                        source_message_id,
                        source_message_id,
                    ),
                )
                applied.append(
                    {"action": "add", "memory_id": int(cursor.lastrowid)}
                )
                continue

            memory_id = int(operation["memory_id"])
            memory = conn.execute(
                """
                SELECT *
                FROM research_task_memories
                WHERE id = ? AND research_task_id = ? AND status = 'active'
                """,
                (memory_id, task_id),
            ).fetchone()
            if memory is None:
                raise ValueError("Task Memory不存在、不属于当前任务或已失效")

            if action == "update":
                category = str(operation["category"])
                content = str(operation["content"])
                duplicate = conn.execute(
                    """
                    SELECT id
                    FROM research_task_memories
                    WHERE research_task_id = ? AND status = 'active'
                      AND category = ? AND content = ? AND id <> ?
                    LIMIT 1
                    """,
                    (task_id, category, content, memory_id),
                ).fetchone()
                if duplicate is not None:
                    conn.execute(
                        """
                        UPDATE research_task_memories
                        SET status = 'removed', updated_from_message_id = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (source_message_id, memory_id),
                    )
                    applied.append(
                        {
                            "action": "merge_duplicate",
                            "memory_id": memory_id,
                            "kept_memory_id": int(duplicate["id"]),
                        }
                    )
                else:
                    conn.execute(
                        """
                        UPDATE research_task_memories
                        SET category = ?, content = ?, updated_from_message_id = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (category, content, source_message_id, memory_id),
                    )
                    applied.append({"action": "update", "memory_id": memory_id})
                continue

            if action == "remove":
                conn.execute(
                    """
                    UPDATE research_task_memories
                    SET status = 'removed', updated_from_message_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (source_message_id, memory_id),
                )
                applied.append({"action": "remove", "memory_id": memory_id})
                continue

            raise ValueError("不支持的Task Memory操作")

        conn.execute(
            """
            UPDATE research_tasks
            SET task_memory_error = NULL,
                task_memory_updated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (task_id,),
        )
    return applied


def record_research_task_memory_error(task_id: int, error: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET task_memory_error = ?,
                task_memory_updated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (str(error or "Task Memory更新失败")[:1000], task_id),
        )
    return cursor.rowcount == 1


def update_research_message(
    message_id: int,
    status: str,
    *,
    content: Optional[str] = None,
    evidence: Optional[list[dict]] = None,
    model: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_messages
            SET status = ?,
                content = COALESCE(?, content),
                evidence_json = COALESCE(?, evidence_json),
                model = COALESCE(?, model),
                error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND role = 'assistant'
            """,
            (
                status,
                content,
                (
                    json.dumps(evidence, ensure_ascii=False)
                    if evidence is not None
                    else None
                ),
                model,
                error[:1000] if error else None,
                message_id,
            ),
        )
    return cursor.rowcount == 1


def mark_research_message_retry_pending(message_id: int, error: str) -> bool:
    return update_research_message(
        message_id,
        "retry_wait",
        error=error,
    )


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


def mark_research_report_retry_pending(task_id: int, error: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_reports
            SET status = 'preparing', error = ?, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
              AND status IN ('preparing', 'retrieving', 'generating', 'failed')
            """,
            (error[:1000], task_id),
        )
    return cursor.rowcount == 1


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


def mark_research_search_retry_pending(task_id: int, error: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET status = 'running', error = ?, completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('running', 'failed')
            """,
            (error[:1000], task_id),
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
                d.parser_backend,
                d.parser_model,
                d.parse_stage,
                d.index_status,
                d.index_error,
                d.indexed_at,
                (
                    SELECT job.status
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'download_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS download_job_status,
                (
                    SELECT job.attempt_count
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'download_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS download_job_attempts,
                (
                    SELECT job.status
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'prepare_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS preparation_job_status,
                (
                    SELECT job.attempt_count
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'prepare_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS preparation_job_attempts,
                (
                    SELECT job.status
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'parse_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS parse_job_status,
                (
                    SELECT job.attempt_count
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'parse_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS parse_job_attempts,
                (
                    SELECT job.status
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'index_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS index_job_status,
                (
                    SELECT job.attempt_count
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'index_paper'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS index_job_attempts,
                (
                    SELECT job.status
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'answer_rag_query'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS rag_job_status,
                (
                    SELECT job.attempt_count
                    FROM background_jobs AS job
                    WHERE job.paper_id = p.id
                      AND job.job_type = 'answer_rag_query'
                    ORDER BY job.id DESC
                    LIMIT 1
                ) AS rag_job_attempts,
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


def update_remote_parse_state(
    paper_id: int,
    *,
    source_hash: str,
    parser_backend: str,
    parser_model: str,
    parser_job_id: Optional[str] = None,
    parse_stage: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_documents (
                paper_id, parse_status, source_hash, parser_backend,
                parser_model, parser_job_id, parse_stage, error
            )
            VALUES (?, 'parsing', ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(paper_id) DO UPDATE SET
                parse_status = 'parsing',
                source_hash = excluded.source_hash,
                parser_backend = excluded.parser_backend,
                parser_model = excluded.parser_model,
                parser_job_id = COALESCE(excluded.parser_job_id, parser_job_id),
                parse_stage = excluded.parse_stage,
                error = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                paper_id,
                source_hash,
                parser_backend,
                parser_model,
                parser_job_id,
                parse_stage,
            ),
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
    parser_backend: Optional[str] = None,
    parser_model: Optional[str] = None,
    parser_job_id: Optional[str] = None,
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
                chunk_role, section_title, content_type, bbox_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    paper_id,
                    chunk.page_number,
                    chunk.chunk_index,
                    chunk.content,
                    parent_ids.get(chunk.parent_index),
                    chunk.role,
                    chunk.section_title,
                    chunk.content_type,
                    (
                        json.dumps(chunk.bbox)
                        if chunk.bbox is not None
                        else None
                    ),
                )
                for chunk in chunk_list
            ],
        )
        conn.execute(
            """
            INSERT INTO paper_documents (
                paper_id, parse_status, page_count, chunk_count, error,
                source_hash, chunk_strategy, chunk_size, chunk_overlap,
                layout_stats_json, parser_backend, parser_model,
                parser_job_id, parse_stage, parsed_at,
                index_status, index_error, vectorizer, embedding_namespace,
                indexed_at
            )
            VALUES (
                ?, 'parsed', ?, ?, NULL, ?, ?, ?, ?,
                ?, ?, ?, ?, 'completed', CURRENT_TIMESTAMP,
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
                parser_backend = excluded.parser_backend,
                parser_model = excluded.parser_model,
                parser_job_id = excluded.parser_job_id,
                parse_stage = 'completed',
                parsed_at = CURRENT_TIMESTAMP,
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
                parser_backend,
                parser_model,
                parser_job_id,
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


def _background_job_from_row(row: sqlite3.Row) -> dict:
    job = dict(row)
    try:
        payload = json.loads(job.get("payload_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    job["payload"] = payload if isinstance(payload, dict) else {}
    return job


def enqueue_research_search_job(
    task_id: int,
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    timestamp = _db_timestamp(now)
    dedupe_key = f"research_search:{task_id}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT id, status FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise LookupError("研究任务不存在")

        active = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE dedupe_key = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if active is not None:
            if task["status"] != "running":
                raise RuntimeError("研究检索队列与任务状态不一致")
            return {
                "job_id": int(active["id"]),
                "task_id": task_id,
                "created": False,
            }

        cursor = conn.execute(
            """
            UPDATE research_tasks
            SET status = 'running', error = NULL, completed_at = NULL,
                confirmed_at = COALESCE(confirmed_at, ?), updated_at = ?
            WHERE id = ? AND status = 'awaiting_confirmation'
            """,
            (timestamp, timestamp, task_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("研究任务当前状态不允许确认")

        job_cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, research_task_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('research_search', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"task_id": task_id}, ensure_ascii=False),
                dedupe_key,
                task_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {
            "job_id": int(job_cursor.lastrowid),
            "task_id": task_id,
            "created": True,
        }


def enqueue_research_report_job(
    task_id: int,
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    timestamp = _db_timestamp(now)
    dedupe_key = f"research_report:{task_id}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT status, selected_count FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise LookupError("研究任务不存在")
        if task["status"] != "papers_selected" or int(task["selected_count"]) < 1:
            raise RuntimeError("研究任务尚未冻结可用于报告的论文")

        active = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE dedupe_key = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        report = conn.execute(
            "SELECT status FROM research_reports WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if active is not None:
            if report is None or report["status"] not in RESEARCH_REPORT_RUNNING:
                raise RuntimeError("研究报告队列与业务状态不一致")
            return {
                "job_id": int(active["id"]),
                "task_id": task_id,
                "created": False,
            }
        if report is not None and report["status"] in RESEARCH_REPORT_RUNNING:
            raise RuntimeError("研究报告已经在执行")

        conn.execute(
            """
            INSERT INTO research_reports (
                task_id, status, content, details_json, model, error,
                started_at, completed_at, updated_at
            )
            VALUES (?, 'preparing', NULL, '{}', NULL, NULL, ?, NULL, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status = 'preparing', content = NULL, details_json = '{}',
                model = NULL, error = NULL, started_at = excluded.started_at,
                completed_at = NULL, updated_at = excluded.updated_at
            """,
            (task_id, timestamp, timestamp),
        )
        job_cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, research_task_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('research_report', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"task_id": task_id}, ensure_ascii=False),
                dedupe_key,
                task_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {
            "job_id": int(job_cursor.lastrowid),
            "task_id": task_id,
            "created": True,
        }


def create_research_followup_messages(task_id: int, question: str) -> dict:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("追问内容不能为空")
    if len(question) > 2000:
        raise ValueError("追问内容不能超过2000个字符")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT status, selected_count FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise LookupError("研究任务不存在")
        if task["status"] != "papers_selected" or int(task["selected_count"]) < 1:
            raise RuntimeError("研究任务尚未冻结论文集合")
        report = conn.execute(
            "SELECT status FROM research_reports WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if report is None or report["status"] != "completed":
            raise RuntimeError("研究报告完成后才能继续追问")
        active_message = conn.execute(
            """
            SELECT id
            FROM research_messages
            WHERE task_id = ?
              AND role = 'assistant'
              AND phase = 'followup'
              AND status IN (
                  'pending', 'retrieving', 'generating', 'streaming', 'retry_wait'
              )
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if active_message is not None:
            raise RuntimeError("上一条研究追问仍在处理中")
        active_job = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE dedupe_key = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (f"research_followup:{task_id}",),
        ).fetchone()
        if active_job is not None:
            raise RuntimeError("上一条研究追问仍在处理中")

        user_cursor = conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'followup', ?)
            """,
            (task_id, question),
        )
        assistant_cursor = conn.execute(
            """
            INSERT INTO research_messages (
                task_id, role, phase, content, status
            )
            VALUES (?, 'assistant', 'followup', '正在分析...', 'pending')
            """,
            (task_id,),
        )
        return {
            "task_id": task_id,
            "user_message_id": int(user_cursor.lastrowid),
            "message_id": int(assistant_cursor.lastrowid),
            "question": question,
        }


def enqueue_research_followup_job(
    task_id: int,
    question: str,
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    question = " ".join(str(question or "").split())
    if not question:
        raise ValueError("追问内容不能为空")
    if len(question) > 2000:
        raise ValueError("追问内容不能超过2000个字符")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    timestamp = _db_timestamp(now)
    dedupe_key = f"research_followup:{task_id}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT status, selected_count FROM research_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise LookupError("研究任务不存在")
        if task["status"] != "papers_selected" or int(task["selected_count"]) < 1:
            raise RuntimeError("研究任务尚未冻结论文集合")
        report = conn.execute(
            "SELECT status FROM research_reports WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if report is None or report["status"] != "completed":
            raise RuntimeError("研究报告完成后才能继续追问")
        active = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE dedupe_key = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if active is not None:
            raise RuntimeError("上一条研究追问仍在处理中")

        user_cursor = conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'followup', ?)
            """,
            (task_id, question),
        )
        assistant_cursor = conn.execute(
            """
            INSERT INTO research_messages (
                task_id, role, phase, content, status
            )
            VALUES (?, 'assistant', 'followup', '正在检索所选论文证据...', 'pending')
            """,
            (task_id,),
        )
        message_id = int(assistant_cursor.lastrowid)
        job_cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, research_task_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('research_followup', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps(
                    {
                        "task_id": task_id,
                        "user_message_id": int(user_cursor.lastrowid),
                        "message_id": message_id,
                        "question": question,
                    },
                    ensure_ascii=False,
                ),
                dedupe_key,
                task_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {
            "job_id": int(job_cursor.lastrowid),
            "task_id": task_id,
            "user_message_id": int(user_cursor.lastrowid),
            "message_id": message_id,
            "created": True,
        }


def enqueue_download_job(
    paper_id: int,
    allowed_statuses: Iterable[str],
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> Optional[int]:
    statuses = tuple(dict.fromkeys(allowed_statuses))
    if not statuses:
        return None
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    timestamp = _db_timestamp(now)
    dedupe_key = f"download_paper:{paper_id}"
    placeholders = ", ".join("?" for _ in statuses)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE paper_id = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        if active is not None:
            return None
        cursor = conn.execute(
            f"""
            UPDATE papers
            SET status = ?, error = NULL, updated_at = ?
            WHERE id = ? AND status IN ({placeholders})
            """,
            (STATUS_DOWNLOADING, timestamp, paper_id, *statuses),
        )
        if cursor.rowcount != 1:
            return None
        job_cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, paper_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('download_paper', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"paper_id": paper_id}, ensure_ascii=False),
                dedupe_key,
                paper_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return int(job_cursor.lastrowid)


def enqueue_paper_preparation_job(
    paper_id: int,
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    timestamp = _db_timestamp(now)
    dedupe_key = f"prepare_paper:{paper_id}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        paper = conn.execute(
            "SELECT * FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise LookupError("论文记录不存在")

        active = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE paper_id = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        if active is not None:
            raise RuntimeError("论文下载或准备任务已经在执行")

        download_claimed = False
        if not paper["local_path"]:
            if paper["status"] not in ("available", "failed"):
                raise RuntimeError("论文当前状态不允许启动准备任务")
            cursor = conn.execute(
                """
                UPDATE papers
                SET status = ?, error = NULL, updated_at = ?
                WHERE id = ? AND status IN ('available', 'failed')
                """,
                (STATUS_DOWNLOADING, timestamp, paper_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("论文准备状态已由其他请求改变")
            preparation_status = STATUS_DOWNLOADING
            download_claimed = True
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO paper_documents (paper_id, parse_status)
                VALUES (?, ?)
                """,
                (paper_id, PARSE_NOT_STARTED),
            )
            document = conn.execute(
                "SELECT * FROM paper_documents WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            if document["parse_status"] == PARSE_PARSING:
                raise RuntimeError("论文解析任务已经在执行")
            if document["parse_status"] != PARSE_PARSED:
                conn.execute(
                    """
                    UPDATE paper_documents
                    SET parse_status = ?, error = NULL, updated_at = ?
                    WHERE paper_id = ?
                    """,
                    (PARSE_PARSING, timestamp, paper_id),
                )
                preparation_status = PARSE_PARSING
            elif document["index_status"] == INDEX_INDEXED:
                return {"job_id": None, "preparation_status": "ready"}
            else:
                if document["index_status"] == INDEX_INDEXING:
                    raise RuntimeError("论文索引任务已经在执行")
                conn.execute(
                    """
                    UPDATE paper_documents
                    SET index_status = ?, index_error = NULL, updated_at = ?
                    WHERE paper_id = ?
                    """,
                    (INDEX_INDEXING, timestamp, paper_id),
                )
                preparation_status = INDEX_INDEXING

        cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, paper_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('prepare_paper', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps(
                    {
                        "paper_id": paper_id,
                        "download_claimed": download_claimed,
                    },
                    ensure_ascii=False,
                ),
                dedupe_key,
                paper_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {
            "job_id": int(cursor.lastrowid),
            "preparation_status": preparation_status,
        }


def _active_paper_job(conn: sqlite3.Connection, paper_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, job_type
        FROM background_jobs
        WHERE paper_id = ?
          AND status IN ('queued', 'running', 'retry_wait')
        ORDER BY id
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()


def enqueue_parse_job(
    paper_id: int,
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    timestamp = _db_timestamp(now)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        paper = conn.execute(
            "SELECT id, local_path FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise LookupError("论文记录不存在")
        if not paper["local_path"]:
            raise RuntimeError("论文PDF尚未下载")
        if _active_paper_job(conn, paper_id) is not None:
            raise RuntimeError("论文已有后台任务正在执行")

        conn.execute(
            """
            INSERT OR IGNORE INTO paper_documents (paper_id, parse_status)
            VALUES (?, ?)
            """,
            (paper_id, PARSE_NOT_STARTED),
        )
        document = conn.execute(
            "SELECT parse_status FROM paper_documents WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if document["parse_status"] == PARSE_PARSING:
            raise RuntimeError("论文解析任务已经在执行")
        queued_parse_status = document["parse_status"]
        if document["parse_status"] != PARSE_PARSED:
            conn.execute(
                """
                UPDATE paper_documents
                SET parse_status = ?, error = NULL, updated_at = ?
                WHERE paper_id = ?
                """,
                (PARSE_PARSING, timestamp, paper_id),
            )
            queued_parse_status = PARSE_PARSING
        cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, paper_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('parse_paper', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"paper_id": paper_id}, ensure_ascii=False),
                f"parse_paper:{paper_id}",
                paper_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {
            "job_id": int(cursor.lastrowid),
            "parse_status": queued_parse_status,
        }


def enqueue_index_job(
    paper_id: int,
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    timestamp = _db_timestamp(now)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        paper = conn.execute(
            "SELECT id FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise LookupError("论文记录不存在")
        if _active_paper_job(conn, paper_id) is not None:
            raise RuntimeError("论文已有后台任务正在执行")
        document = conn.execute(
            "SELECT parse_status, index_status FROM paper_documents WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if document is None or document["parse_status"] != PARSE_PARSED:
            raise RuntimeError("论文尚未完成解析")
        if document["index_status"] == INDEX_INDEXING:
            raise RuntimeError("论文索引任务已经在执行")
        conn.execute(
            """
            UPDATE paper_documents
            SET index_status = ?, index_error = NULL, updated_at = ?
            WHERE paper_id = ?
            """,
            (INDEX_INDEXING, timestamp, paper_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, paper_id, status,
                max_attempts, available_at, created_at, updated_at
            )
            VALUES ('index_paper', ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"paper_id": paper_id}, ensure_ascii=False),
                f"index_paper:{paper_id}",
                paper_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {"job_id": int(cursor.lastrowid), "index_status": INDEX_INDEXING}


def enqueue_rag_answer_job(
    paper_id: int,
    question: str,
    reranker: str = "none",
    *,
    max_attempts: int = 3,
    now: Optional[datetime] = None,
) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("question is required")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    timestamp = _db_timestamp(now)
    fingerprint = hashlib.sha256(
        f"{reranker}\0{question}".encode("utf-8")
    ).hexdigest()[:32]
    dedupe_key = f"answer_rag_query:{paper_id}:{fingerprint}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        paper = conn.execute(
            "SELECT id FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise LookupError("论文记录不存在")
        document = conn.execute(
            "SELECT index_status FROM paper_documents WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()
        if document is None or document["index_status"] != INDEX_INDEXED:
            raise RuntimeError("论文尚未完成索引")

        blocking_job = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE paper_id = ?
              AND job_type != 'answer_rag_query'
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        if blocking_job is not None:
            raise RuntimeError("论文已有准备任务正在执行")

        active = conn.execute(
            """
            SELECT id, rag_query_id
            FROM background_jobs
            WHERE dedupe_key = ?
              AND status IN ('queued', 'running', 'retry_wait')
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()
        if active is not None:
            if active["rag_query_id"] is None:
                raise RuntimeError("活动问答任务缺少关联记录")
            return {
                "job_id": int(active["id"]),
                "query_id": int(active["rag_query_id"]),
                "created": False,
            }

        query_cursor = conn.execute(
            """
            INSERT INTO rag_queries (paper_id, question, reranker, status)
            VALUES (?, ?, ?, ?)
            """,
            (paper_id, question, reranker, RAG_ANSWERING),
        )
        query_id = int(query_cursor.lastrowid)
        job_cursor = conn.execute(
            """
            INSERT INTO background_jobs (
                job_type, payload_json, dedupe_key, paper_id, rag_query_id,
                status, max_attempts, available_at, created_at, updated_at
            )
            VALUES ('answer_rag_query', ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"query_id": query_id}, ensure_ascii=False),
                dedupe_key,
                paper_id,
                query_id,
                max_attempts,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return {
            "job_id": int(job_cursor.lastrowid),
            "query_id": query_id,
            "created": True,
        }


def get_background_job(job_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM background_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _background_job_from_row(row) if row is not None else None


def list_background_jobs(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM background_jobs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_background_job_from_row(row) for row in rows]


def claim_next_background_job(
    worker_id: str,
    *,
    lease_seconds: int = 60,
    job_types: Iterable[str] = (),
    now: Optional[datetime] = None,
) -> Optional[dict]:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id is required")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")

    types = tuple(dict.fromkeys(job_types))
    current = now or _utc_now()
    timestamp = _db_timestamp(current)
    lease_expires_at = _db_timestamp(current + timedelta(seconds=lease_seconds))
    type_clause = ""
    parameters: list[object] = [timestamp]
    if types:
        placeholders = ", ".join("?" for _ in types)
        type_clause = f" AND job_type IN ({placeholders})"
        parameters.extend(types)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"""
            SELECT id
            FROM background_jobs
            WHERE status IN ('queued', 'retry_wait')
              AND available_at <= ?
              {type_clause}
            ORDER BY priority DESC, id
            LIMIT 1
            """,
            tuple(parameters),
        ).fetchone()
        if row is None:
            return None
        job_id = int(row["id"])
        cursor = conn.execute(
            """
            UPDATE background_jobs
            SET status = 'running',
                worker_id = ?,
                lease_expires_at = ?,
                attempt_count = attempt_count + 1,
                started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE id = ?
              AND status IN ('queued', 'retry_wait')
              AND available_at <= ?
            """,
            (
                worker_id,
                lease_expires_at,
                timestamp,
                timestamp,
                job_id,
                timestamp,
            ),
        )
        if cursor.rowcount != 1:
            return None
        claimed = conn.execute(
            "SELECT * FROM background_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _background_job_from_row(claimed)


def renew_background_job_lease(
    job_id: int,
    worker_id: str,
    *,
    lease_seconds: int = 60,
    now: Optional[datetime] = None,
) -> bool:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id is required")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")
    current = now or _utc_now()
    timestamp = _db_timestamp(current)
    lease_expires_at = _db_timestamp(current + timedelta(seconds=lease_seconds))
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE background_jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ?
              AND status = 'running'
              AND worker_id = ?
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at > ?
            """,
            (lease_expires_at, timestamp, job_id, worker_id, timestamp),
        )
    return cursor.rowcount == 1


def complete_background_job(
    job_id: int,
    worker_id: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    timestamp = _db_timestamp(now)
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE background_jobs
            SET status = 'succeeded',
                lease_expires_at = NULL,
                last_error = NULL,
                completed_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'running' AND worker_id = ?
            """,
            (timestamp, timestamp, job_id, worker_id),
        )
    return cursor.rowcount == 1


def fail_background_job(
    job_id: int,
    worker_id: str,
    error: str,
    *,
    retry_delay_seconds: int = 5,
    now: Optional[datetime] = None,
) -> Optional[str]:
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds cannot be negative")
    current = now or _utc_now()
    timestamp = _db_timestamp(current)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT attempt_count, max_attempts
            FROM background_jobs
            WHERE id = ? AND status = 'running' AND worker_id = ?
            """,
            (job_id, worker_id),
        ).fetchone()
        if row is None:
            return None
        should_retry = int(row["attempt_count"]) < int(row["max_attempts"])
        next_status = "retry_wait" if should_retry else "failed"
        available_at = _db_timestamp(
            current + timedelta(seconds=retry_delay_seconds)
        )
        conn.execute(
            """
            UPDATE background_jobs
            SET status = ?,
                available_at = ?,
                lease_expires_at = NULL,
                worker_id = NULL,
                last_error = ?,
                completed_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                available_at,
                error[:1000],
                next_status,
                timestamp,
                timestamp,
                job_id,
            ),
        )
    return next_status


def recover_expired_background_jobs(
    *,
    now: Optional[datetime] = None,
) -> list[dict]:
    timestamp = _db_timestamp(now)
    recovered: list[dict] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT *
            FROM background_jobs
            WHERE status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            ORDER BY id
            """,
            (timestamp,),
        ).fetchall()
        for row in rows:
            next_status = (
                "queued"
                if int(row["attempt_count"]) < int(row["max_attempts"])
                else "failed"
            )
            error = "Worker lease expired before the task completed"
            conn.execute(
                """
                UPDATE background_jobs
                SET status = ?,
                    available_at = ?,
                    lease_expires_at = NULL,
                    worker_id = NULL,
                    last_error = ?,
                    completed_at = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    next_status,
                    timestamp,
                    error,
                    next_status,
                    timestamp,
                    timestamp,
                    int(row["id"]),
                ),
            )
            item = _background_job_from_row(row)
            item["status"] = next_status
            item["last_error"] = error
            recovered.append(item)
    return recovered


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

import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pymysql
import pytest

from my_agent_project import config, db
from my_agent_project.mysql_migrations import apply_mysql_migrations
from my_agent_project.schemas import PaperCandidate, PaperChunk, PaperParentChunk


MYSQL_INTEGRATION_ENABLED = os.getenv(
    "PAPER_HUNTER_MYSQL_TEST",
    "",
).lower() in {"1", "true", "yes", "on"}


class RecordingCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.rows = [
            {
                "id": 7,
                "created_at": datetime(2026, 8, 27, 1, 2, 3, 456789),
            }
        ]
        self.rowcount = 1
        self.lastrowid = 7

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def executemany(self, sql, params):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows

    def close(self):
        return None

    def __iter__(self):
        return iter(self.rows)


class RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = RecordingCursor()

    def cursor(self):
        return self.cursor_instance


def test_mysql_placeholder_translation_preserves_literals_and_comments(
    monkeypatch,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    sql = "SELECT '?' AS literal, id FROM sample WHERE a = ? AND b = ? -- ?\n/* ? */"

    assert db.mysql_compatible_sql(sql) == (
        "SELECT '?' AS literal, id FROM sample WHERE a = %s AND b = %s -- ?\n"
        "/* ? */"
    )
    assert db.parameter_placeholders(3) == "%s, %s, %s"


def test_sqlite_dynamic_in_placeholders_remain_qmark(monkeypatch) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "sqlite")

    assert db.parameter_placeholders(3) == "?, ?, ?"
    with pytest.raises(ValueError, match="at least 1"):
        db.parameter_placeholders(0)


def test_mysql_adapter_supports_execute_rows_rowcount_and_lastrowid(
    monkeypatch,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    raw = RecordingConnection()
    connection = db.MySQLConnectionAdapter(raw)

    cursor = connection.execute("SELECT * FROM sample WHERE id = ?", (7,))
    row = cursor.fetchone()

    assert raw.cursor_instance.statements == [
        ("SELECT * FROM sample WHERE id = %s", (7,))
    ]
    assert row["id"] == 7
    assert dict(row)["id"] == 7
    assert row["created_at"] == "2026-08-27T01:02:03.456789+00:00"
    assert cursor.rowcount == 1
    assert cursor.lastrowid == 7


def test_mysql_active_dedupe_lookup_uses_generated_unique_key(monkeypatch) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    raw = RecordingConnection()
    connection = db.MySQLConnectionAdapter(raw)

    row = db._active_job_by_dedupe_key(connection, "download_paper:7")

    sql, params = raw.cursor_instance.statements[-1]
    assert row["id"] == 7
    assert "WHERE active_dedupe_key = %s" in sql
    assert "status IN" not in sql
    assert params == ("download_paper:7",)


class IntegrityConnection:
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return object()


@pytest.mark.parametrize(
    "error",
    [
        pymysql.err.IntegrityError(1452, "foreign key"),
        pymysql.err.IntegrityError(1048, "not null"),
        pymysql.err.DataError(1406, "invalid data"),
    ],
)
def test_duplicate_only_ignore_reraises_non_duplicate_errors(
    monkeypatch,
    error,
) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    connection = IntegrityConnection(error)

    with pytest.raises(type(error)):
        db.execute_insert_ignoring_duplicate(
            connection,
            sqlite_sql="INSERT OR IGNORE INTO sample VALUES (?)",
            mysql_sql="INSERT INTO sample VALUES (?)",
            params=(1,),
            mysql_unique_key="PRIMARY",
        )


def test_duplicate_only_ignore_suppresses_mysql_1062(monkeypatch) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    connection = IntegrityConnection(
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry '1' for key 'sample.PRIMARY'",
        )
    )

    assert db.execute_insert_ignoring_duplicate(
        connection,
        sqlite_sql="INSERT OR IGNORE INTO sample VALUES (?)",
        mysql_sql="INSERT INTO sample VALUES (?)",
        params=(1,),
        mysql_unique_key="PRIMARY",
    ) is None
    assert connection.calls == 1


def test_duplicate_only_ignore_reraises_unrelated_mysql_1062(monkeypatch) -> None:
    monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
    connection = IntegrityConnection(
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry '1' for key 'sample.unrelated_unique'",
        )
    )

    with pytest.raises(pymysql.err.IntegrityError):
        db.execute_insert_ignoring_duplicate(
            connection,
            sqlite_sql="INSERT OR IGNORE INTO sample VALUES (?)",
            mysql_sql="INSERT INTO sample VALUES (?)",
            params=(1,),
            mysql_unique_key="PRIMARY",
        )


def test_phase3_sql_dialect_inventory_is_explicit() -> None:
    source = Path("my_agent_project/db.py").read_text(encoding="utf-8")

    assert source.count("ON CONFLICT") == 12
    assert source.count("ON DUPLICATE KEY UPDATE") == 12
    assert source.count("INSERT OR IGNORE") == 2
    assert "INSERT IGNORE" not in source
    assert source.count('conn.execute("BEGIN IMMEDIATE")') == 6
    assert "PHASE_4_BLOCKED" not in source
    assert "Phase4BlockedError" not in source


@pytest.mark.skipif(
    not MYSQL_INTEGRATION_ENABLED,
    reason="set PAPER_HUNTER_MYSQL_TEST=1 to run the MySQL CRUD test",
)
def test_mysql_business_crud_in_isolated_database(monkeypatch) -> None:
    database_name = f"paper_hunter_phase3_{uuid4().hex}"
    admin = pymysql.connect(
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
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        created = True
        monkeypatch.setattr(db, "BUSINESS_DB_BACKEND", "mysql")
        monkeypatch.setattr(db, "BUSINESS_DB_NAME", database_name)
        connection = db.connect()
        try:
            apply_mysql_migrations(connection)
        finally:
            connection.close()

        long_summary = "长文摘要" * 5000
        paper_id = db.insert_papers(
            "中文检索",
            [
                PaperCandidate(
                    title="Paper Hunter 中文论文",
                    authors="测试作者",
                    summary=long_summary,
                    published="2026-08-27",
                    source="test",
                    publisher="测试出版社",
                    doi=None,
                    page_url="https://example.test/paper",
                    pdf_url=None,
                    status="available",
                )
            ],
        )[0]
        paper = db.get_paper(paper_id)
        assert paper["id"] == paper_id
        assert dict(paper)["summary"] == long_summary
        assert paper["doi"] is None
        assert db.list_papers(1)[0]["id"] == paper_id
        db.update_download_status(paper_id, "failed", error="test error")
        assert db.get_paper(paper_id)["status"] == "failed"
        db.update_download_status(paper_id, "available")

        task_id = db.create_research_task(
            question="研究中文问题",
            max_results=5,
            plan_goal="目标",
            plan_queries=["检索词 A", "query B"],
            plan_rationale="理由",
            plan_model="test-model",
            planner_status="completed",
            planner_error="",
            status="awaiting_confirmation",
            checkpoint_thread_id="phase3-thread",
        )
        assert db.get_research_task(task_id)["question"] == "研究中文问题"
        assert db.revise_research_task_plan(
            task_id,
            question="更新问题",
            plan_goal="更新目标",
            plan_queries=["updated query"],
            plan_rationale="updated",
            plan_model="test-model",
            planner_status="completed",
            planner_error="",
            user_message="请更新",
        )
        assert db.list_research_tasks(1)[0]["plan_queries"] == ["updated query"]

        messages = db.list_research_messages(task_id)
        assert [message["id"] for message in messages] == sorted(
            message["id"] for message in messages
        )
        assistant_id = next(
            message["id"] for message in messages if message["role"] == "assistant"
        )
        assert db.update_research_message(
            assistant_id,
            "completed",
            content="更新后消息",
            evidence=[],
        )

        db.replace_research_tool_calls(
            task_id,
            [
                {
                    "tool_name": "search_literature",
                    "input": {"query": "test"},
                    "status": "success",
                    "result_count": 1,
                }
            ],
        )
        assert db.list_research_tool_calls(task_id)[0]["result_count"] == 1

        with db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO research_reports (task_id, status, content)
                VALUES (?, ?, ?)
                """,
                (task_id, "generating", "draft"),
            )
            connection.execute(
                "UPDATE research_tasks SET question = question WHERE id = ?",
                (task_id,),
            )
            assert connection.execute(
                "UPDATE research_tasks SET question = question WHERE id = ?",
                (task_id,),
            ).rowcount == 1
        db.update_research_report(
            task_id,
            "completed",
            content="完成报告",
            details={"language": "zh"},
            model="test-model",
        )
        assert db.get_research_report(task_id)["details"] == {"language": "zh"}

        assert db.get_or_create_document(paper_id)["paper_id"] == paper_id
        assert db.get_or_create_document(paper_id)["paper_id"] == paper_id
        with pytest.raises(pymysql.err.IntegrityError):
            db.get_or_create_document(paper_id + 999999)
        with pytest.raises(pymysql.err.IntegrityError):
            db.update_parse_status(paper_id + 999999, "parsing")

        insert_paper_sql = """
            INSERT INTO papers (
                query, title, authors, summary, published, source,
                publisher, page_url, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with pytest.raises(pymysql.err.IntegrityError):
            with db.transaction() as connection:
                db.execute_insert_ignoring_duplicate(
                    connection,
                    sqlite_sql=insert_paper_sql,
                    mysql_sql=insert_paper_sql,
                    mysql_unique_key="PRIMARY",
                    params=(
                        "query",
                        None,
                        "author",
                        "summary",
                        "2026",
                        "test",
                        "publisher",
                        "https://example.test/not-null",
                        "available",
                    ),
                )
        with pytest.raises(pymysql.err.DataError):
            with db.transaction() as connection:
                db.execute_insert_ignoring_duplicate(
                    connection,
                    sqlite_sql=insert_paper_sql,
                    mysql_sql=insert_paper_sql,
                    mysql_unique_key="PRIMARY",
                    params=(
                        "query",
                        "invalid source",
                        "author",
                        "summary",
                        "2026",
                        "x" * 65,
                        "publisher",
                        "https://example.test/invalid-data",
                        "available",
                    ),
                )

        db.replace_paper_chunks(
            paper_id,
            [PaperChunk(1, 0, "子块", parent_index=0)],
            page_count=1,
            parent_chunks=[PaperParentChunk(1, 0, "父块")],
            source_hash="a" * 64,
            chunk_strategy="test",
            chunk_size=1200,
            chunk_overlap=150,
        )
        assert db.list_paper_chunks(paper_id)[0]["content"] == "子块"
        db.update_parse_status(paper_id, "parsed", page_count=1, chunk_count=1)
        db.update_parse_status(paper_id, "parsing", page_count=2, chunk_count=0)
        assert db.get_document(paper_id)["page_count"] == 2
        db.update_index_status(paper_id, "indexed", vectorizer="test-vectorizer")

        source_hash = "b" * 64
        assert db.replace_cached_pdf_pages(source_hash, ["第一页", "page two"]) == 2
        assert db.get_cached_pdf_pages(source_hash) == ["第一页", "page two"]
        hashes = {"c" * 64: [0.1], "d" * 64: [0.2]}
        assert db.upsert_cached_embeddings("phase3", hashes) == 2
        assert db.get_cached_embeddings("phase3", hashes) == hashes
        db.upsert_cached_embeddings("phase3", {"c" * 64: [0.9]})
        assert db.get_cached_embeddings("phase3", ["c" * 64]) == {
            "c" * 64: [0.9]
        }
        db.upsert_cached_llm_response("phase3", "e" * 64, {"answer": "初版"})
        db.upsert_cached_llm_response("phase3", "e" * 64, {"answer": "更新"})
        assert db.get_cached_llm_response("phase3", "e" * 64) == {
            "answer": "更新"
        }

        rag_id = db.create_rag_query(paper_id, "这篇论文说了什么？")
        db.update_rag_query(
            rag_id,
            "answered",
            answer="答案",
            evidence=[{"page": 1}],
            model="test-model",
        )
        assert db.get_rag_query(rag_id)["answer"] == "答案"

        trace_id = db.create_agent_trace(
            task_id=task_id,
            thread_id="phase3-thread",
            node_name="compatibility",
            start_time="2026-08-27T00:00:00+00:00",
        )
        db.finish_agent_trace(
            trace_id,
            status="success",
            end_time="2026-08-27T00:00:01+00:00",
            duration_ms=1000,
        )
        assert db.list_agent_traces(task_id=task_id)[0]["status"] == "success"

        case_id = db.upsert_evaluation_case(
            paper_id, "fact", "question", "answer v1", 1, "evidence v1"
        )
        assert db.upsert_evaluation_case(
            paper_id, "fact", "question", "answer v2", 2, "evidence v2"
        ) == case_id
        assert db.list_evaluation_cases()[0]["reference_answer"] == "answer v2"
        run_id = db.create_evaluation_run(
            name="phase3",
            chunk_strategy="test",
            query_mode="original",
            keyword_retriever="tfidf",
            chunk_size=1200,
            chunk_overlap=150,
            embedding_model="test",
            semantic_weight=0.75,
            keyword_weight=0.25,
            top_k=5,
            case_count=1,
        )
        db.save_evaluation_result(
            run_id,
            case_id,
            hit_rank=1,
            reciprocal_rank=1.0,
            best_coverage=0.8,
            retrieved=[{"id": 1}],
        )
        db.save_evaluation_result(
            run_id,
            case_id,
            hit_rank=2,
            reciprocal_rank=0.5,
            best_coverage=0.9,
            retrieved=[{"id": 2}],
        )
        assert db.list_evaluation_results(run_id)[0]["hit_rank"] == 2

        generation_run_id = db.create_generation_evaluation_run(
            name="phase3-generation",
            retrieval_run_id=run_id,
            generator_model="test",
            judge_model="judge",
            prompt_version="v1",
            case_count=1,
        )
        result_args = dict(
            answer="answer",
            evidence=[{"page": 1}],
            faithfulness=1.0,
            answer_relevance=1.0,
            reference_correctness=1.0,
            citation_correctness=1.0,
            judge={"ok": True},
            generation_seconds=0.1,
            judge_seconds=0.1,
        )
        db.save_generation_evaluation_result(
            generation_run_id, case_id, **result_args
        )
        db.save_generation_evaluation_result(
            generation_run_id,
            case_id,
            **{**result_args, "answer": "updated answer"},
        )
        assert db.list_generation_evaluation_results(generation_run_id)[0][
            "answer"
        ] == "updated answer"

        with pytest.raises(RuntimeError, match="rollback phase3"):
            with db.transaction() as connection:
                connection.execute(
                    "UPDATE papers SET title = ? WHERE id = ?",
                    ("must rollback", paper_id),
                )
                raise RuntimeError("rollback phase3")
        assert db.get_paper(paper_id)["title"] == "Paper Hunter 中文论文"
    finally:
        if created:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database_name}`")
        admin.close()


def test_langgraph_checkpoint_module_was_not_modified_by_phase3() -> None:
    source = Path("my_agent_project/research_checkpoint.py").read_text(
        encoding="utf-8"
    )

    assert "SqliteSaver" in source
    assert "LANGGRAPH_CHECKPOINT_PATH" in source

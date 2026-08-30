"""Run one real Query Routing Research Chat in an isolated SQLite database.

The script reads one frozen paper and its Original Paper Chunks from the formal
MySQL database, copies them into a temporary SQLite Business Database, and then
uses the real Seed/DeepSeek services. Formal MySQL remains read-only and the
temporary SQLite directory is removed when the run ends.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import gc
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import pymysql
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GROUND_TRUTH = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "benchmarks"
    / "retrieval_fusion_ab_v1.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "retrieval_query_routing_chat_e2e_v1.json"
)
E2E_QUESTION = "这篇论文最后得出了什么结论？"
CITATION_PATTERN = re.compile(r"\[[^\[\]]+，第\d+页\]")


def _source_connection():
    return pymysql.connect(
        host=os.environ.get("BUSINESS_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("BUSINESS_DB_PORT", "3306")),
        user=os.environ["BUSINESS_DB_USER"],
        password=os.environ.get("BUSINESS_DB_PASSWORD", ""),
        database=os.environ.get("BUSINESS_DB_NAME", "paper_hunter"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _load_frozen_paper() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ground_truth = json.loads(DEFAULT_GROUND_TRUTH.read_text(encoding="utf-8"))
    paper_id = int(ground_truth["papers"][0]["paper_id"])
    source = _source_connection()
    try:
        with source.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute("SELECT * FROM papers WHERE id = %s", (paper_id,))
            paper = cursor.fetchone()
            cursor.execute(
                "SELECT * FROM paper_documents WHERE paper_id = %s",
                (paper_id,),
            )
            document = cursor.fetchone()
            cursor.execute(
                """
                SELECT * FROM paper_chunks
                WHERE paper_id = %s
                ORDER BY chunk_index, id
                """,
                (paper_id,),
            )
            chunks = cursor.fetchall()
            source.rollback()
    finally:
        source.close()
    if paper is None or document is None or not chunks:
        raise RuntimeError("冻结论文或索引数据不存在")
    return paper, document, list(chunks)


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _copy_rows(conn, table: str, rows: list[Mapping[str, Any]]) -> None:
    if not rows:
        return
    available = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    columns = [column for column in rows[0] if column in available]
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    for row in rows:
        conn.execute(
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
            tuple(_sqlite_value(row[column]) for column in columns),
        )


def main() -> None:
    load_dotenv(ROOT / ".env")
    paper, document, chunks = _load_frozen_paper()
    formal_database = os.environ.get("BUSINESS_DB_NAME", "paper_hunter")

    temp_root = ROOT / "PaperHunterData" / "smolagent-tests" / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="paper-hunter-query-routing-e2e-",
        dir=temp_root,
    ) as tmp:
        os.environ["BUSINESS_DB_BACKEND"] = "sqlite"
        os.environ["DOWNLOAD_DIR"] = tmp
        os.environ["RESEARCH_QUERY_ROUTING_ENABLED"] = "true"

        from my_agent_project import db, research_conversation_service

        db.init_db()
        with db.transaction() as conn:
            _copy_rows(conn, "papers", [paper])
            _copy_rows(conn, "paper_documents", [document])
            _copy_rows(conn, "paper_chunks", chunks)

        paper_id = int(paper["id"])
        task_id = db.create_research_task(
            question="验证跨语言论文检索",
            max_results=5,
            plan_goal="验证中文研究问题对英文论文证据的检索",
            plan_queries=["cross-lingual academic retrieval"],
            plan_rationale="isolated real E2E",
            plan_model="deterministic-e2e",
            planner_status="completed",
            planner_error="",
            status="running",
            sources=("arxiv",),
        )
        db.save_research_task_results(task_id, [paper_id])
        if not db.select_research_task_papers(task_id, [paper_id]):
            raise RuntimeError("无法创建隔离的已选论文任务")
        messages = db.create_research_followup_messages(task_id, E2E_QUESTION)
        events = list(
            research_conversation_service.stream_research_followup(
                messages["message_id"],
                E2E_QUESTION,
                current_user_message_id=messages["user_message_id"],
            )
        )
        assistant = db.get_research_message(messages["message_id"])
        done = next((payload for event, payload in events if event == "done"), None)
        if done is None or assistant is None:
            error = next(
                (payload.get("message", "") for event, payload in events if event == "error"),
                "Research Chat 未完成",
            )
            raise RuntimeError(error)
        routing = done.get("query_routing") or {}
        answer = str(assistant.get("content") or "")
        evidence = assistant.get("evidence") or []
        result = {
            "version": "retrieval-query-routing-chat-e2e-v1",
            "business_database": "temporary SQLite",
            "formal_mysql_database": formal_database,
            "formal_mysql_access": "read-only source copy",
            "formal_mysql_writes": 0,
            "paper_id": paper_id,
            "paper_title": paper["title"],
            "original_query": E2E_QUESTION,
            "query_routing": routing,
            "assistant_status": assistant["status"],
            "assistant_model": assistant["model"],
            "answer": answer,
            "evidence_count": len(evidence),
            "evidence_chunk_ids": [int(item["chunk_id"]) for item in evidence],
            "citation_present": bool(CITATION_PATTERN.search(answer)),
            "original_query_preserved": routing.get("original_query") == E2E_QUESTION,
            "english_lexical_query_generated": (
                routing.get("query_rewrite_status") in {"generated", "cache_hit"}
                and routing.get("lexical_query") != E2E_QUESTION
            ),
            "lexical_query_not_in_evidence": all(
                routing.get("lexical_query") not in json.dumps(item, ensure_ascii=False)
                for item in evidence
            ),
            "temporary_database_removed_after_run": True,
        }
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"output={DEFAULT_OUTPUT}")
        del assistant, evidence, events
        gc.collect()


if __name__ == "__main__":
    main()

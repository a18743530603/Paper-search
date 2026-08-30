import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException, Response

from my_agent_project import db, main, paper_summary_service, task_queue_service
from my_agent_project.schemas import PaperCandidate, PaperChunk


def _candidate(title: str) -> PaperCandidate:
    return PaperCandidate(
        title=title,
        authors="Summary Author",
        summary="Metadata abstract must not become paper evidence.",
        published="2026-08-28",
        source="arxiv",
        publisher="arXiv",
        doi=None,
        page_url=f"https://example.test/{title}",
        pdf_url=f"https://example.test/{title}.pdf",
        status="downloaded",
    )


def _setup_database(tmp_path, monkeypatch) -> tuple[int, int]:
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    return tuple(
        db.insert_papers("paper summary", [_candidate("Paper A"), _candidate("Paper B")])
    )


def _create_selected_task(paper_ids: list[int]) -> int:
    task_id = db.create_research_task(
        question="Find papers",
        max_results=5,
        plan_goal="Read selected papers",
        plan_queries=["paper summary"],
        plan_rationale="test",
        plan_model="test",
        planner_status="planned",
        planner_error="",
        status="awaiting_confirmation",
    )
    assert db.start_research_task(task_id)
    db.save_research_task_results(task_id, paper_ids)
    assert db.select_research_task_papers(task_id, paper_ids)
    return task_id


def _mark_indexed(paper_id: int, sections: list[tuple[str, int, str]]) -> None:
    db.get_or_create_document(paper_id)
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(
                page_number=page,
                chunk_index=index,
                content=content,
                section_title=section,
            )
            for index, (section, page, content) in enumerate(sections)
        ],
        page_count=max(page for _section, page, _content in sections),
    )
    db.update_parse_status(
        paper_id,
        "parsed",
        page_count=max(page for _section, page, _content in sections),
        chunk_count=len(sections),
    )
    db.update_index_status(paper_id, "indexed", vectorizer="summary-test")


def _default_sections(title: str) -> list[tuple[str, int, str]]:
    return [
        ("1 Introduction", 1, f"{title} studies a grounded research problem."),
        ("2 Method", 3, f"{title} uses a structured evidence method."),
        ("3 Experiments", 5, f"{title} evaluates the method on a test dataset."),
        ("4 Results", 6, f"{title} reports an evidence-backed improvement."),
        ("5 Conclusion", 8, f"{title} concludes that the method is effective."),
    ]


def test_selected_indexed_paper_summary_completes_as_independent_artifact(
    tmp_path,
    monkeypatch,
):
    paper_a, _paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    _mark_indexed(paper_a, _default_sections("Paper A"))
    messages_before = db.list_research_messages(task_id)
    monkeypatch.setattr(
        paper_summary_service,
        "ask_deepseek",
        lambda _system, prompt: (
            "【主要方法】\nPaper A uses grounded evidence [Paper A, p. 3].",
            "deepseek-summary-test",
        ),
    )

    queued = db.enqueue_paper_summary_job(task_id, paper_a)
    result = task_queue_service.run_one_job("summary-worker")
    summary = db.get_paper_summary(task_id, paper_a)

    assert queued["created"] is True
    assert result["status"] == "succeeded"
    assert summary["task_id"] == task_id
    assert summary["paper_id"] == paper_a
    assert summary["status"] == "completed"
    assert summary["model"] == "deepseek-summary-test"
    assert "[Paper A，第3页]" in summary["content"]
    assert {item["section_title"] for item in summary["evidence"]} >= {
        "1 Introduction",
        "2 Method",
        "3 Experiments",
        "4 Results",
        "5 Conclusion",
    }
    assert db.list_research_messages(task_id) == messages_before


def test_unselected_paper_is_rejected(tmp_path, monkeypatch):
    paper_a, paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    _mark_indexed(paper_b, _default_sections("Paper B"))

    with pytest.raises(RuntimeError, match="已选择"):
        db.enqueue_paper_summary_job(task_id, paper_b)

    assert db.get_paper_summary(task_id, paper_b) is None
    assert db.list_background_jobs() == []


def test_unprepared_selected_paper_does_not_enter_generation(tmp_path, monkeypatch):
    paper_a, _paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])

    with pytest.raises(RuntimeError, match="解析和索引"):
        db.enqueue_paper_summary_job(task_id, paper_a)

    assert db.get_paper_summary(task_id, paper_a) is None
    assert db.list_background_jobs() == []


def test_summary_job_deduplicates_and_retries_with_existing_queue_semantics(
    tmp_path,
    monkeypatch,
):
    paper_a, _paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    _mark_indexed(paper_a, _default_sections("Paper A"))
    calls = []

    def flaky_model(_system, _prompt):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary model failure")
        return "Paper A result [Paper A, p. 6].", "deepseek-summary-test"

    monkeypatch.setattr(paper_summary_service, "ask_deepseek", flaky_model)
    first = db.enqueue_paper_summary_job(task_id, paper_a, max_attempts=2)
    repeated = db.enqueue_paper_summary_job(task_id, paper_a, max_attempts=2)

    assert repeated["created"] is False
    assert repeated["job_id"] == first["job_id"]
    assert repeated["summary_id"] == first["summary_id"]
    first_run = task_queue_service.run_one_job(
        "summary-worker",
        retry_delay_seconds=0,
    )
    waiting = db.get_paper_summary(task_id, paper_a)
    second_run = task_queue_service.run_one_job(
        "summary-worker",
        retry_delay_seconds=0,
    )

    assert first_run["status"] == "retry_wait"
    assert waiting["status"] == "queued"
    assert waiting["completed_at"] is None
    assert "waiting for retry" in waiting["error"]
    assert second_run["status"] == "succeeded"
    assert db.get_paper_summary(task_id, paper_a)["status"] == "completed"
    assert db.get_background_job(first["job_id"])["attempt_count"] == 2


def test_no_original_evidence_is_deterministic_and_never_calls_model(
    tmp_path,
    monkeypatch,
):
    paper_a, _paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    db.get_or_create_document(paper_a)
    db.update_parse_status(paper_a, "parsed", page_count=0, chunk_count=0)
    db.update_index_status(paper_a, "indexed", vectorizer="summary-test")
    monkeypatch.setattr(
        paper_summary_service,
        "ask_deepseek",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not run without Original Paper Evidence")
        ),
    )

    db.enqueue_paper_summary_job(task_id, paper_a)
    result = task_queue_service.run_one_job("summary-worker")
    summary = db.get_paper_summary(task_id, paper_a)

    assert result["status"] == "succeeded"
    assert summary["status"] == "completed"
    assert summary["model"] == "deterministic-no-evidence"
    assert summary["evidence"] == []
    assert "不会根据标题、摘要或外部知识补写" in summary["content"]


def test_two_selected_papers_have_independent_summaries(tmp_path, monkeypatch):
    paper_a, paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a, paper_b])
    _mark_indexed(paper_a, _default_sections("Paper A"))
    _mark_indexed(paper_b, _default_sections("Paper B"))

    def summary_model(_system, prompt):
        payload_text = prompt.split(
            "BEGIN_UNTRUSTED_PAPER_SUMMARY_EVIDENCE_JSON\n", 1
        )[1].split("\nEND_UNTRUSTED_PAPER_SUMMARY_EVIDENCE_JSON", 1)[0]
        title = json.loads(payload_text)["paper"]["paper_title"]
        return f"{title} summary [{title}, p. 1].", "deepseek-summary-test"

    monkeypatch.setattr(paper_summary_service, "ask_deepseek", summary_model)
    first = db.enqueue_paper_summary_job(task_id, paper_a)
    second = db.enqueue_paper_summary_job(task_id, paper_b)
    task_queue_service.run_one_job("summary-worker")
    task_queue_service.run_one_job("summary-worker")
    summaries = db.list_paper_summaries(task_id)

    assert first["summary_id"] != second["summary_id"]
    assert {(item["paper_id"], item["status"]) for item in summaries} == {
        (paper_a, "completed"),
        (paper_b, "completed"),
    }
    assert "Paper A summary" in db.get_paper_summary(task_id, paper_a)["content"]
    assert "Paper B summary" in db.get_paper_summary(task_id, paper_b)["content"]


def test_summary_api_supports_polling_and_regeneration_without_cross_task_access(
    tmp_path,
    monkeypatch,
):
    paper_a, paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    _mark_indexed(paper_a, _default_sections("Paper A"))

    response = Response()
    created = main.create_paper_summary_route(task_id, paper_a, response)
    polled = main.get_paper_summary_route(task_id, paper_a)
    repeated = main.create_paper_summary_route(task_id, paper_a, Response())

    assert response.status_code == 202
    assert created["created"] is True
    assert created["summary"]["status"] == "queued"
    assert polled["summary"]["content"] is None
    assert repeated["created"] is False
    with pytest.raises(HTTPException) as exc_info:
        main.create_paper_summary_route(task_id, paper_b, Response())
    assert exc_info.value.status_code == 409


def test_summary_api_for_paper_a_does_not_create_paper_b_summary(
    tmp_path,
    monkeypatch,
):
    paper_a, paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a, paper_b])
    _mark_indexed(paper_a, _default_sections("Paper A"))
    _mark_indexed(paper_b, _default_sections("Paper B"))

    result = main.create_paper_summary_route(task_id, paper_a, Response())

    assert result["summary"]["paper_id"] == paper_a
    assert db.get_paper_summary(task_id, paper_a) is not None
    assert db.get_paper_summary(task_id, paper_b) is None
    summary_jobs = [
        job for job in db.list_background_jobs() if job["job_type"] == "paper_summary"
    ]
    assert [job["paper_id"] for job in summary_jobs] == [paper_a]


def test_summary_does_not_gate_research_chat_or_create_optional_report(
    tmp_path,
    monkeypatch,
):
    paper_a, _paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    _mark_indexed(paper_a, _default_sections("Paper A"))
    monkeypatch.setattr(
        paper_summary_service,
        "ask_deepseek",
        lambda *_args: ("Paper A summary [Paper A, p. 1].", "summary-model"),
    )
    db.enqueue_paper_summary_job(task_id, paper_a)
    task_queue_service.run_one_job("summary-worker")

    followup = db.create_research_followup_messages(task_id, "What is the method?")
    report_job = db.enqueue_research_report_job(task_id)

    assert followup["message_id"] > 0
    assert report_job["created"] is True
    assert db.get_research_report(task_id)["status"] == "preparing"
    assert db.get_paper_summary(task_id, paper_a)["status"] == "completed"


def test_expired_final_summary_lease_marks_artifact_failed(tmp_path, monkeypatch):
    paper_a, _paper_b = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_task([paper_a])
    _mark_indexed(paper_a, _default_sections("Paper A"))
    queued = db.enqueue_paper_summary_job(task_id, paper_a, max_attempts=1)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "summary-worker-before-crash",
        lease_seconds=10,
        now=started_at,
        job_types=["paper_summary"],
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()
    summary = db.get_paper_summary(task_id, paper_a)

    assert recovered[0]["status"] == "failed"
    assert summary["status"] == "failed"
    assert "lease expired" in summary["error"]


def test_structure_coverage_prefers_major_sections_without_query_top_k():
    chunks = [
        {
            "id": index + 1,
            "paper_id": 1,
            "page_number": index + 1,
            "chunk_index": index,
            "content": f"Evidence for {section}",
            "section_title": section,
            "content_type": "text",
        }
        for index, section in enumerate(
            [
                "Abstract",
                "Introduction",
                "Related Work",
                "Method",
                "Architecture",
                "Training",
                "Experimental Setup",
                "Dataset",
                "Metrics",
                "Results",
                "Ablation",
                "Discussion",
                "Limitations",
                "Future Work",
                "Conclusion",
                "Appendix",
                "Additional Analysis",
                "Final Notes",
            ]
        )
    ]

    evidence = paper_summary_service.select_paper_summary_evidence(chunks)
    sections = {item["section_title"] for item in evidence}

    assert len(evidence) <= paper_summary_service.PAPER_SUMMARY_MAX_EVIDENCE
    assert {"Abstract", "Method", "Experimental Setup", "Results", "Conclusion"} <= sections
    assert evidence == sorted(evidence, key=lambda item: item["chunk_index"])

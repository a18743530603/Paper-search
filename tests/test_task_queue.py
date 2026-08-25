import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event

from my_agent_project import (
    db,
    rag_service,
    research_conversation_service,
    task_queue_service,
)
from my_agent_project.schemas import PaperCandidate


def _create_downloadable_paper() -> PaperCandidate:
    return PaperCandidate(
        title="Durable Queue Paper",
        authors="Test Author",
        summary="Queue test",
        published="2026-08-17",
        source="arxiv",
        publisher="arXiv",
        doi=None,
        page_url="https://arxiv.org/abs/2608.00001",
        pdf_url="https://arxiv.org/pdf/2608.00001",
        status="available",
    )


def _setup_database(tmp_path, monkeypatch) -> int:
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()
    return db.insert_papers("queue", [_create_downloadable_paper()])[0]


def _mark_paper_downloaded(paper_id: int, tmp_path) -> None:
    db.update_download_status(
        paper_id,
        "downloaded",
        local_path=str(tmp_path / "paper.pdf"),
    )


def _mark_paper_indexed(paper_id: int, tmp_path) -> None:
    _mark_paper_downloaded(paper_id, tmp_path)
    db.update_parse_status(paper_id, "parsed", page_count=1, chunk_count=1)
    db.update_index_status(paper_id, "indexed", vectorizer="test")


def _create_research_task() -> int:
    return db.create_research_task(
        question="multimodal research agents",
        max_results=5,
        plan_goal="Find research agent papers",
        plan_queries=["multimodal research agents", "agentic literature review"],
        plan_rationale="Cover the main terminology.",
        plan_model="deepseek-test",
        planner_status="planned",
        planner_error="",
        status="awaiting_confirmation",
    )


def _create_selected_research_task(paper_id: int) -> int:
    task_id = _create_research_task()
    assert db.start_research_task(task_id)
    db.save_research_task_results(task_id, [paper_id])
    assert db.select_research_task_papers(task_id, [paper_id])
    return task_id


def _complete_research_report(task_id: int) -> None:
    db.update_research_report(
        task_id,
        "completed",
        content="Existing evidence-grounded report.",
        model="deepseek-test",
    )


def test_research_task_starts_with_conversation_and_separate_report_question(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()

    task = db.get_research_task(task_id)
    messages = db.list_research_messages(task_id)

    assert task["report_question"] == ""
    assert [(item["role"], item["phase"]) for item in messages] == [
        ("user", "discovery"),
        ("assistant", "planning"),
    ]
    assert "Find research agent papers" in messages[1]["content"]


def test_revise_plan_and_set_report_question_are_persisted_as_messages(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()
    revised = db.revise_research_task_plan(
        task_id,
        question="RAG evaluation with a faithfulness focus",
        plan_goal="Compare RAG evaluation metrics",
        plan_queries=["RAG faithfulness evaluation", "RAG retrieval recall metrics"],
        plan_rationale="Cover generation and retrieval quality.",
        plan_model="deepseek-test",
        planner_status="planned",
        planner_error="",
        user_message="重点关注Faithfulness",
    )
    assert revised is True
    assert db.start_research_task(task_id)
    db.save_research_task_results(task_id, [paper_id])
    assert db.select_research_task_papers(task_id, [paper_id])
    assert db.set_research_report_question(task_id, "两种指标有什么区别？")

    task = db.get_research_task(task_id)
    messages = db.list_research_messages(task_id)

    assert task["plan_goal"] == "Compare RAG evaluation metrics"
    assert task["report_question"] == "两种指标有什么区别？"
    assert [item["phase"] for item in messages[-4:]] == [
        "planning",
        "planning",
        "report_question",
        "report_question",
    ]


def test_download_enqueue_is_durable_across_database_reinitialization(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)

    job_id = db.enqueue_download_job(paper_id, ("available",))
    db.init_db()

    assert job_id is not None
    assert db.get_paper(paper_id)["status"] == "downloading"
    job = db.get_background_job(job_id)
    assert job["status"] == "queued"
    assert job["payload"] == {"paper_id": paper_id}


def test_research_search_enqueue_is_atomic_durable_and_deduplicated(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()

    first = db.enqueue_research_search_job(task_id)
    repeated = db.enqueue_research_search_job(task_id)
    db.init_db()

    assert first["created"] is True
    assert repeated == {
        "job_id": first["job_id"],
        "task_id": task_id,
        "created": False,
    }
    task = db.get_research_task(task_id)
    assert task["status"] == "running"
    assert task["search_job_status"] == "queued"
    assert task["search_job_attempts"] == 0
    assert task["search_job_max_attempts"] == 3
    assert len(db.list_background_jobs()) == 1


def test_concurrent_research_confirmations_share_one_job(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()
    barrier = Barrier(2)

    def confirm_at_the_same_time():
        barrier.wait()
        return db.enqueue_research_search_job(task_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _index: confirm_at_the_same_time(), range(2))
        )

    assert {result["job_id"] for result in results} == {results[0]["job_id"]}
    assert sorted(result["created"] for result in results) == [False, True]
    assert len(db.list_background_jobs()) == 1


def test_registered_research_search_handler_reuses_workflow(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()
    queued = db.enqueue_research_search_job(task_id)
    calls = []

    def fake_execute(received_task_id, *, raise_on_error=False):
        calls.append((received_task_id, raise_on_error))
        db.save_research_task_results(received_task_id, [])

    monkeypatch.setattr(
        task_queue_service,
        "execute_research_task",
        fake_execute,
    )

    result = task_queue_service.run_one_job("worker-a")

    assert result["status"] == "succeeded"
    assert calls == [(task_id, True)]
    assert db.get_background_job(queued["job_id"])["status"] == "succeeded"
    assert db.get_research_task(task_id)["status"] == "awaiting_selection"


def test_research_search_worker_retries_then_completes(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()
    queued = db.enqueue_research_search_job(task_id, max_attempts=2)
    calls = []

    def flaky_execute(received_task_id, *, raise_on_error=False):
        calls.append((received_task_id, raise_on_error))
        if len(calls) == 1:
            db.fail_research_task(received_task_id, "temporary search outage")
            raise RuntimeError("temporary search outage")
        db.save_research_task_results(received_task_id, [])

    monkeypatch.setattr(
        task_queue_service,
        "execute_research_task",
        flaky_execute,
    )

    first = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )
    waiting = db.get_research_task(task_id)
    second = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )

    assert first["status"] == "retry_wait"
    assert waiting["status"] == "running"
    assert "waiting for retry" in waiting["error"]
    assert second["status"] == "succeeded"
    assert len(calls) == 2
    assert db.get_background_job(queued["job_id"])["attempt_count"] == 2
    assert db.get_research_task(task_id)["status"] == "awaiting_selection"


def test_expired_final_research_search_lease_marks_task_failed(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()
    queued = db.enqueue_research_search_job(task_id, max_attempts=1)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()

    assert recovered[0]["status"] == "failed"
    task = db.get_research_task(task_id)
    assert task["status"] == "failed"
    assert "lease expired" in task["error"]


def test_recovered_research_job_does_not_repeat_completed_search(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_research_task()
    queued = db.enqueue_research_search_job(task_id, max_attempts=2)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    db.save_research_task_results(task_id, [])
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )
    calls = []
    monkeypatch.setattr(
        task_queue_service,
        "execute_research_task",
        lambda *_args, **_kwargs: calls.append("unexpected"),
    )

    recovered = task_queue_service.recover_expired_jobs()
    result = task_queue_service.run_one_job("worker-after-crash")

    assert recovered[0]["status"] == "queued"
    assert result["status"] == "succeeded"
    assert calls == []
    assert db.get_research_task(task_id)["status"] == "awaiting_selection"


def test_research_report_enqueue_is_atomic_durable_and_deduplicated(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)

    first = db.enqueue_research_report_job(task_id)
    repeated = db.enqueue_research_report_job(task_id)
    db.init_db()

    assert first["created"] is True
    assert repeated == {
        "job_id": first["job_id"],
        "task_id": task_id,
        "created": False,
    }
    report = db.get_research_report(task_id)
    assert report["status"] == "preparing"
    assert report["job_status"] == "queued"
    assert report["job_attempts"] == 0
    assert report["job_max_attempts"] == 3
    assert len(db.list_background_jobs()) == 1


def test_concurrent_research_report_requests_share_one_job(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    barrier = Barrier(2)

    def generate_at_the_same_time():
        barrier.wait()
        return db.enqueue_research_report_job(task_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _index: generate_at_the_same_time(), range(2))
        )

    assert {result["job_id"] for result in results} == {results[0]["job_id"]}
    assert sorted(result["created"] for result in results) == [False, True]
    assert len(db.list_background_jobs()) == 1


def test_registered_research_report_handler_reuses_workflow(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    queued = db.enqueue_research_report_job(task_id)
    calls = []

    def fake_execute(received_task_id):
        calls.append(received_task_id)
        db.update_research_report(
            received_task_id,
            "completed",
            content="report",
            model="deepseek-test",
        )

    monkeypatch.setattr(
        task_queue_service,
        "execute_research_report",
        fake_execute,
    )

    result = task_queue_service.run_one_job("worker-a")
    regenerated = db.enqueue_research_report_job(task_id)

    assert result["status"] == "succeeded"
    assert calls == [task_id]
    assert db.get_background_job(queued["job_id"])["status"] == "succeeded"
    assert regenerated["created"] is True
    assert regenerated["job_id"] != queued["job_id"]
    assert db.get_research_report(task_id)["status"] == "preparing"


def test_research_report_worker_retries_then_completes(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    queued = db.enqueue_research_report_job(task_id, max_attempts=2)
    calls = []

    def flaky_execute(received_task_id):
        calls.append(received_task_id)
        if len(calls) == 1:
            db.update_research_report(
                received_task_id,
                "failed",
                error="temporary model outage",
            )
            return
        db.update_research_report(
            received_task_id,
            "completed",
            content="report",
            model="deepseek-test",
        )

    monkeypatch.setattr(
        task_queue_service,
        "execute_research_report",
        flaky_execute,
    )

    first = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )
    waiting = db.get_research_report(task_id)
    second = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )

    assert first["status"] == "retry_wait"
    assert waiting["status"] == "preparing"
    assert "waiting for retry" in waiting["error"]
    assert second["status"] == "succeeded"
    assert calls == [task_id, task_id]
    assert db.get_background_job(queued["job_id"])["attempt_count"] == 2
    assert db.get_research_report(task_id)["status"] == "completed"


def test_expired_final_research_report_lease_marks_report_failed(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    queued = db.enqueue_research_report_job(task_id, max_attempts=1)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()

    assert recovered[0]["status"] == "failed"
    report = db.get_research_report(task_id)
    assert report["status"] == "failed"
    assert "lease expired" in report["error"]


def test_recovered_report_job_does_not_repeat_completed_generation(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    queued = db.enqueue_research_report_job(task_id, max_attempts=2)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    db.update_research_report(
        task_id,
        "completed",
        content="already persisted",
        model="deepseek-test",
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )
    calls = []
    monkeypatch.setattr(
        task_queue_service,
        "execute_research_report",
        lambda *_args, **_kwargs: calls.append("unexpected"),
    )

    recovered = task_queue_service.recover_expired_jobs()
    result = task_queue_service.run_one_job("worker-after-crash")

    assert recovered[0]["status"] == "queued"
    assert result["status"] == "succeeded"
    assert calls == []
    report = db.get_research_report(task_id)
    assert report["status"] == "completed"
    assert report["content"] == "already persisted"


def test_research_followup_enqueue_and_worker_complete_message(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    db.start_research_report(task_id)
    _complete_research_report(task_id)
    queued = db.enqueue_research_followup_job(task_id, "What evidence supports it?")

    def fake_answer(message_id, question, *, raise_on_error=False):
        assert question == "What evidence supports it?"
        assert raise_on_error is True
        db.update_research_message(
            message_id,
            "completed",
            content="Evidence-based follow-up answer.",
            evidence=[{"paper_id": paper_id, "page_number": 2}],
            model="deepseek-test",
        )

    monkeypatch.setattr(
        task_queue_service,
        "answer_research_followup",
        fake_answer,
    )

    result = task_queue_service.run_one_job("worker-a")
    message = db.get_research_message(queued["message_id"])

    assert result["status"] == "succeeded"
    assert message["status"] == "completed"
    assert message["content"] == "Evidence-based follow-up answer."
    assert message["evidence"] == [{"paper_id": paper_id, "page_number": 2}]


def test_research_followup_without_evidence_completes_without_model(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    task_id = _create_selected_research_task(paper_id)
    db.start_research_report(task_id)
    _complete_research_report(task_id)
    queued = db.enqueue_research_followup_job(task_id, "Explain the missing result")

    monkeypatch.setattr(
        research_conversation_service,
        "retrieve_report_paper_evidence",
        lambda *_args, **_kwargs: {
            "evidence": [],
            "access_limitation": {
                "paper_id": paper_id,
                "paper_title": "Durable Queue Paper",
                "reason": "全文不可用",
                "url": "https://example.com/paper",
            },
        },
    )
    monkeypatch.setattr(
        research_conversation_service,
        "ask_deepseek",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("model must not run without evidence")
        ),
    )

    research_conversation_service.answer_research_followup(
        queued["message_id"],
        "Explain the missing result",
        raise_on_error=True,
    )
    message = db.get_research_message(queued["message_id"])

    assert message["status"] == "completed"
    assert message["model"] == "deterministic-no-evidence"
    assert "不能可靠作答" in message["content"]


def test_download_enqueue_atomically_rejects_duplicate_requests(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    barrier = Barrier(2)

    def enqueue_at_the_same_time():
        barrier.wait()
        return db.enqueue_download_job(paper_id, ("available",))

    with ThreadPoolExecutor(max_workers=2) as executor:
        job_ids = list(executor.map(lambda _index: enqueue_at_the_same_time(), range(2)))

    assert sum(job_id is not None for job_id in job_ids) == 1
    assert len(db.list_background_jobs()) == 1
    assert db.get_paper(paper_id)["status"] == "downloading"


def test_worker_retries_failed_download_then_completes(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(
        paper_id,
        ("available",),
        max_attempts=2,
    )
    calls = []

    def flaky_download(payload):
        calls.append(payload["paper_id"])
        if len(calls) == 1:
            raise RuntimeError("temporary network failure")
        db.update_download_status(
            paper_id,
            "downloaded",
            local_path=str(tmp_path / "paper.pdf"),
        )

    handlers = {task_queue_service.JOB_DOWNLOAD_PAPER: flaky_download}

    first = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
        handlers=handlers,
    )
    second = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
        handlers=handlers,
    )

    assert first["status"] == "retry_wait"
    assert second["status"] == "succeeded"
    assert calls == [paper_id, paper_id]
    assert db.get_background_job(job_id)["attempt_count"] == 2
    assert db.get_background_job(job_id)["status"] == "succeeded"
    assert db.get_paper(paper_id)["status"] == "downloaded"


def test_registered_download_handler_reuses_download_service(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(paper_id, ("available",))
    calls = []

    def fake_download(received_paper_id, *, raise_on_error=False):
        calls.append((received_paper_id, raise_on_error))
        db.update_download_status(
            received_paper_id,
            "downloaded",
            local_path=str(tmp_path / "paper.pdf"),
        )

    monkeypatch.setattr(task_queue_service, "download_paper", fake_download)

    result = task_queue_service.run_one_job("worker-a")

    assert result["status"] == "succeeded"
    assert calls == [(paper_id, True)]
    assert db.get_background_job(job_id)["status"] == "succeeded"


def test_expired_worker_lease_requeues_job_for_another_worker(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(paper_id, ("available",), max_attempts=2)
    available_at = datetime.fromisoformat(
        db.get_background_job(job_id)["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)

    first_claim = db.claim_next_background_job(
        "worker-before-restart",
        lease_seconds=10,
        now=started_at,
    )
    recovered = db.recover_expired_background_jobs(
        now=started_at + timedelta(seconds=11),
    )
    second_claim = db.claim_next_background_job(
        "worker-after-restart",
        lease_seconds=10,
        now=started_at + timedelta(seconds=11),
    )

    assert first_claim["id"] == job_id
    assert recovered[0]["status"] == "queued"
    assert second_claim["id"] == job_id
    assert second_claim["attempt_count"] == 2
    assert second_claim["worker_id"] == "worker-after-restart"


def test_worker_stops_after_max_attempts_and_marks_paper_failed(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(
        paper_id,
        ("available",),
        max_attempts=2,
    )

    def always_fails(_payload):
        raise RuntimeError("permanent download failure")

    handlers = {task_queue_service.JOB_DOWNLOAD_PAPER: always_fails}
    task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
        handlers=handlers,
    )
    result = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
        handlers=handlers,
    )

    assert result["status"] == "failed"
    assert db.get_background_job(job_id)["status"] == "failed"
    paper = db.get_paper(paper_id)
    assert paper["status"] == "failed"
    assert paper["error"] == "permanent download failure"


def test_preparation_enqueue_atomically_claims_download_stage(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)

    queued = db.enqueue_paper_preparation_job(paper_id)
    db.init_db()

    assert queued["preparation_status"] == "downloading"
    assert db.get_paper(paper_id)["status"] == "downloading"
    job = db.get_background_job(queued["job_id"])
    assert job["job_type"] == task_queue_service.JOB_PREPARE_PAPER
    assert job["payload"] == {
        "paper_id": paper_id,
        "download_claimed": True,
    }
    assert job["status"] == "queued"


def test_preparation_enqueue_preserves_active_parse_across_web_restart(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)

    queued = db.enqueue_paper_preparation_job(paper_id)
    db.init_db()

    assert queued["preparation_status"] == "parsing"
    assert db.get_document(paper_id)["parse_status"] == "parsing"
    assert db.get_background_job(queued["job_id"])["status"] == "queued"


def test_registered_preparation_handler_reuses_paper_workflow(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    queued = db.enqueue_paper_preparation_job(paper_id)
    calls = []

    def fake_prepare(received_paper_id, *, download_claimed=False):
        calls.append((received_paper_id, download_claimed))
        _mark_paper_downloaded(received_paper_id, tmp_path)
        db.update_parse_status(received_paper_id, "parsed", page_count=1, chunk_count=1)
        db.update_index_status(received_paper_id, "indexed", vectorizer="test")
        return "ready"

    monkeypatch.setattr(task_queue_service, "prepare_paper_for_qa", fake_prepare)

    result = task_queue_service.run_one_job("worker-a")

    assert result["status"] == "succeeded"
    assert calls == [(paper_id, True)]
    assert db.get_background_job(queued["job_id"])["status"] == "succeeded"


def test_preparation_worker_retries_failed_parse_then_completes(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    queued = db.enqueue_paper_preparation_job(paper_id, max_attempts=2)
    calls = []

    def flaky_prepare(received_paper_id, *, download_claimed=False):
        calls.append((received_paper_id, download_claimed))
        if len(calls) == 1:
            db.update_parse_status(received_paper_id, "parse_failed", error="bad pdf")
            return "parse_failed"
        db.update_parse_status(received_paper_id, "parsed", page_count=1, chunk_count=1)
        db.update_index_status(received_paper_id, "indexed", vectorizer="test")
        return "ready"

    monkeypatch.setattr(task_queue_service, "prepare_paper_for_qa", flaky_prepare)

    first = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )
    status_while_waiting = db.get_document(paper_id)["parse_status"]
    second = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )

    assert first["status"] == "retry_wait"
    assert status_while_waiting == "parsing"
    assert second["status"] == "succeeded"
    assert calls == [(paper_id, False), (paper_id, False)]
    assert db.get_background_job(queued["job_id"])["attempt_count"] == 2


def test_active_download_job_blocks_preparation_enqueue(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    db.enqueue_download_job(paper_id, ("available",))

    try:
        db.enqueue_paper_preparation_job(paper_id)
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError("Preparation enqueue should reject an active download")

    assert "已经在执行" in error
    assert len(db.list_background_jobs()) == 1


def test_expired_final_preparation_lease_marks_parse_failed(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    queued = db.enqueue_paper_preparation_job(paper_id, max_attempts=1)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()

    assert recovered[0]["status"] == "failed"
    document = db.get_document(paper_id)
    assert document["parse_status"] == "parse_failed"
    assert "lease expired" in document["error"]


def test_parse_enqueue_is_persistent_and_allows_explicit_reparse(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)

    queued = db.enqueue_parse_job(paper_id)
    db.init_db()

    assert queued["parse_status"] == "parsing"
    assert db.get_document(paper_id)["parse_status"] == "parsing"
    assert db.get_background_job(queued["job_id"])["job_type"] == "parse_paper"

    claimed = db.claim_next_background_job("worker-a")
    db.update_parse_status(paper_id, "parsed", page_count=1, chunk_count=1)
    assert db.complete_background_job(claimed["id"], "worker-a") is True
    repeated = db.enqueue_parse_job(paper_id)

    assert repeated["job_id"] is not None
    assert repeated["parse_status"] == "parsed"
    assert len(db.list_background_jobs()) == 2


def test_registered_parse_handler_reuses_pdf_service(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    queued = db.enqueue_parse_job(paper_id)
    calls = []

    def fake_parse(received_paper_id):
        calls.append(received_paper_id)
        db.update_parse_status(received_paper_id, "parsed", page_count=1, chunk_count=1)

    monkeypatch.setattr(task_queue_service, "parse_paper_pdf", fake_parse)

    result = task_queue_service.run_one_job("worker-a")

    assert result["status"] == "succeeded"
    assert calls == [paper_id]
    assert db.get_background_job(queued["job_id"])["status"] == "succeeded"

def test_parse_worker_retries_then_completes(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    queued = db.enqueue_parse_job(paper_id, max_attempts=2)
    calls = []

    def flaky_parse(received_paper_id):
        calls.append(received_paper_id)
        if len(calls) == 1:
            db.update_parse_status(received_paper_id, "parse_failed", error="bad pdf")
        else:
            db.update_parse_status(
                received_paper_id,
                "parsed",
                page_count=1,
                chunk_count=1,
            )

    monkeypatch.setattr(task_queue_service, "parse_paper_pdf", flaky_parse)

    first = task_queue_service.run_one_job("worker-a", retry_delay_seconds=0)
    waiting_status = db.get_document(paper_id)["parse_status"]
    second = task_queue_service.run_one_job("worker-a", retry_delay_seconds=0)

    assert first["status"] == "retry_wait"
    assert waiting_status == "parsing"
    assert second["status"] == "succeeded"
    assert calls == [paper_id, paper_id]
    assert db.get_background_job(queued["job_id"])["attempt_count"] == 2


def test_index_enqueue_is_persistent_and_handler_reuses_rag_service(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    db.update_parse_status(paper_id, "parsed", page_count=1, chunk_count=1)
    queued = db.enqueue_index_job(paper_id)
    db.init_db()
    calls = []

    def fake_index(received_paper_id):
        calls.append(received_paper_id)
        db.update_index_status(received_paper_id, "indexed", vectorizer="test")

    monkeypatch.setattr(task_queue_service, "index_paper_chunks", fake_index)

    result = task_queue_service.run_one_job("worker-a")

    assert queued["index_status"] == "indexing"
    assert result["status"] == "succeeded"
    assert calls == [paper_id]
    assert db.get_background_job(queued["job_id"])["status"] == "succeeded"

    repeated = db.enqueue_index_job(paper_id)
    assert repeated["job_id"] is not None
    assert repeated["index_status"] == "indexing"


def test_active_parse_job_blocks_other_paper_jobs(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    db.enqueue_parse_job(paper_id)

    for enqueue in (
        db.enqueue_paper_preparation_job,
        db.enqueue_index_job,
    ):
        try:
            enqueue(paper_id)
        except RuntimeError as exc:
            assert "任务" in str(exc)
        else:
            raise AssertionError("An active parse job should block other paper jobs")

    assert db.enqueue_download_job(paper_id, ("downloaded",)) is None
    assert len(db.list_background_jobs()) == 1


def test_expired_final_index_lease_marks_index_failed(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_downloaded(paper_id, tmp_path)
    db.update_parse_status(paper_id, "parsed", page_count=1, chunk_count=1)
    queued = db.enqueue_index_job(paper_id, max_attempts=1)
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()

    assert recovered[0]["status"] == "failed"
    document = db.get_document(paper_id)
    assert document["index_status"] == "index_failed"
    assert "lease expired" in document["index_error"]


def test_rag_answer_enqueue_is_atomic_and_deduplicates_active_question(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)

    first = db.enqueue_rag_answer_job(paper_id, "What dataset is used?")
    repeated = db.enqueue_rag_answer_job(paper_id, "What dataset is used?")
    db.init_db()

    assert first["created"] is True
    assert repeated == {
        "job_id": first["job_id"],
        "query_id": first["query_id"],
        "created": False,
    }
    assert len(db.list_background_jobs()) == 1
    assert len(db.list_rag_queries(paper_id)) == 1
    assert db.get_rag_query(first["query_id"])["status"] == "answering"


def test_init_db_adds_rag_query_link_to_existing_job_table(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE background_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                dedupe_key TEXT NOT NULL,
                paper_id INTEGER,
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
                completed_at TEXT
            )
            """
        )

    db.init_db()

    with sqlite3.connect(database_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(background_jobs)").fetchall()
        }
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(background_jobs)").fetchall()
        }
    assert "rag_query_id" in columns
    assert "idx_background_jobs_rag_query" in indexes


def test_rag_answer_enqueue_allows_different_questions_and_blocks_reindex(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)

    first = db.enqueue_rag_answer_job(paper_id, "What dataset is used?")
    second = db.enqueue_rag_answer_job(paper_id, "What metric is reported?")

    assert first["query_id"] != second["query_id"]
    assert len(db.list_background_jobs()) == 2
    try:
        db.enqueue_index_job(paper_id)
    except RuntimeError as exc:
        assert "任务" in str(exc)
    else:
        raise AssertionError("An active answer job should block reindexing")


def test_registered_rag_answer_handler_reuses_rag_service(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)
    queued = db.enqueue_rag_answer_job(paper_id, "What dataset is used?")
    calls = []

    def fake_answer(query_id, *, raise_on_error=False):
        calls.append((query_id, raise_on_error))
        db.update_rag_query(query_id, "answered", answer="CIFAR-10", model="test")

    monkeypatch.setattr(task_queue_service, "answer_rag_query", fake_answer)

    result = task_queue_service.run_one_job("worker-a")

    assert result["status"] == "succeeded"
    assert calls == [(queued["query_id"], True)]
    assert db.get_rag_query(queued["query_id"])["answer"] == "CIFAR-10"


def test_rag_answer_worker_retries_then_completes(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)
    queued = db.enqueue_rag_answer_job(
        paper_id,
        "What dataset is used?",
        max_attempts=2,
    )
    calls = []

    def flaky_answer(query_id, *, raise_on_error=False):
        calls.append((query_id, raise_on_error))
        if len(calls) == 1:
            db.update_rag_query(query_id, "answer_failed", error="temporary timeout")
            raise RuntimeError("temporary timeout")
        db.update_rag_query(query_id, "answered", answer="CIFAR-10", model="test")

    monkeypatch.setattr(task_queue_service, "answer_rag_query", flaky_answer)

    first = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )
    waiting = db.get_rag_query(queued["query_id"])
    second = task_queue_service.run_one_job(
        "worker-a",
        retry_delay_seconds=0,
    )

    assert first["status"] == "retry_wait"
    assert waiting["status"] == "answering"
    assert "waiting for retry" in waiting["error"]
    assert second["status"] == "succeeded"
    assert len(calls) == 2
    assert db.get_rag_query(queued["query_id"])["status"] == "answered"


def test_expired_final_rag_answer_lease_marks_query_failed(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)
    queued = db.enqueue_rag_answer_job(
        paper_id,
        "What dataset is used?",
        max_attempts=1,
    )
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()

    assert recovered[0]["status"] == "failed"
    query = db.get_rag_query(queued["query_id"])
    assert query["status"] == "answer_failed"
    assert "lease expired" in query["error"]


def test_init_db_marks_orphaned_answering_query_failed(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)
    query_id = db.create_rag_query(paper_id, "What dataset is used?")

    db.init_db()

    query = db.get_rag_query(query_id)
    assert query["status"] == "answer_failed"
    assert "应用重启中断" in query["error"]


def test_completed_rag_query_skips_duplicate_model_call(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)
    query_id = db.create_rag_query(paper_id, "What dataset is used?")
    db.update_rag_query(query_id, "answered", answer="CIFAR-10", model="test")

    def unexpected_model_call(*_args, **_kwargs):
        raise AssertionError("Completed answer must not call the model again")

    monkeypatch.setattr(rag_service, "ask_deepseek", unexpected_model_call)

    rag_service.answer_rag_query(query_id, raise_on_error=True)

    query = db.get_rag_query(query_id)
    assert query["status"] == "answered"
    assert query["answer"] == "CIFAR-10"


def test_recovered_answer_job_preserves_completed_answer(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    _mark_paper_indexed(paper_id, tmp_path)
    queued = db.enqueue_rag_answer_job(
        paper_id,
        "What dataset is used?",
        max_attempts=2,
    )
    available_at = datetime.fromisoformat(
        db.get_background_job(queued["job_id"])["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    db.claim_next_background_job(
        "worker-before-crash",
        lease_seconds=10,
        now=started_at,
    )
    db.update_rag_query(
        queued["query_id"],
        "answered",
        answer="CIFAR-10",
        model="test",
    )
    monkeypatch.setattr(
        db,
        "_utc_now",
        lambda: started_at + timedelta(seconds=11),
    )

    recovered = task_queue_service.recover_expired_jobs()
    result = task_queue_service.run_one_job("worker-after-crash")

    assert recovered[0]["status"] == "queued"
    assert result["status"] == "succeeded"
    query = db.get_rag_query(queued["query_id"])
    assert query["status"] == "answered"
    assert query["answer"] == "CIFAR-10"


def test_only_current_worker_can_renew_unexpired_lease(tmp_path, monkeypatch):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(paper_id, ("available",))
    available_at = datetime.fromisoformat(
        db.get_background_job(job_id)["available_at"]
    )
    started_at = available_at + timedelta(seconds=1)
    claimed = db.claim_next_background_job(
        "worker-a",
        lease_seconds=10,
        now=started_at,
    )

    wrong_worker = db.renew_background_job_lease(
        job_id,
        "worker-b",
        lease_seconds=10,
        now=started_at + timedelta(seconds=5),
    )
    renewed = db.renew_background_job_lease(
        job_id,
        "worker-a",
        lease_seconds=10,
        now=started_at + timedelta(seconds=5),
    )
    expired = db.renew_background_job_lease(
        job_id,
        "worker-a",
        lease_seconds=10,
        now=started_at + timedelta(seconds=16),
    )

    assert claimed["worker_id"] == "worker-a"
    assert wrong_worker is False
    assert renewed is True
    assert expired is False


def test_worker_heartbeat_prevents_long_job_from_being_recovered(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(paper_id, ("available",))
    started = Event()
    release = Event()

    def long_download(_payload):
        started.set()
        if not release.wait(timeout=4):
            raise RuntimeError("test did not release long task")
        db.update_download_status(
            paper_id,
            "downloaded",
            local_path=str(tmp_path / "paper.pdf"),
        )

    handlers = {task_queue_service.JOB_DOWNLOAD_PAPER: long_download}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            task_queue_service.run_one_job,
            "worker-heartbeat",
            lease_seconds=2,
            heartbeat_interval_seconds=0.2,
            handlers=handlers,
        )
        assert started.wait(timeout=2)
        initial_expiry = db.get_background_job(job_id)["lease_expires_at"]
        try:
            time.sleep(2.25)
            active_job = db.get_background_job(job_id)
            recovered = db.recover_expired_background_jobs()
        finally:
            release.set()
        result = future.result(timeout=4)

    assert active_job["lease_expires_at"] > initial_expiry
    assert recovered == []
    assert result["status"] == "succeeded"
    assert db.get_background_job(job_id)["status"] == "succeeded"


def test_worker_reports_lost_lease_while_recording_handler_failure(
    tmp_path,
    monkeypatch,
):
    paper_id = _setup_database(tmp_path, monkeypatch)
    job_id = db.enqueue_download_job(paper_id, ("available",))

    def fail_after_losing_ownership(_payload):
        with db.connect() as conn:
            conn.execute(
                "UPDATE background_jobs SET worker_id = 'worker-b' WHERE id = ?",
                (job_id,),
            )
        raise RuntimeError("download failed after ownership changed")

    handlers = {
        task_queue_service.JOB_DOWNLOAD_PAPER: fail_after_losing_ownership,
    }
    try:
        task_queue_service.run_one_job("worker-a", handlers=handlers)
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError("Lost lease must be reported to the worker")

    assert "lease was lost" in error
    assert "download failed after ownership changed" in error
    job = db.get_background_job(job_id)
    assert job["status"] == "running"
    assert job["worker_id"] == "worker-b"

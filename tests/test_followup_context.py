import json

import pytest

from my_agent_project import (
    research_conversation_service,
    research_evidence_service,
)
from my_agent_project.followup_context import build_followup_context


def _complete_context():
    return build_followup_context(
        "当前问题",
        "已有报告",
        [
            {
                "paper_id": 1,
                "paper_title": "Paper A",
                "page_number": 3,
                "chunk_id": 8,
                "score": 0.92,
                "content": "Evidence text.",
            }
        ],
        [
            {
                "paper_id": 2,
                "paper_title": "Paper B",
                "url": "https://example.com/paper-b",
                "reason": "全文不可用",
            }
        ],
        task_memory=[
            {"id": 5, "category": "preference", "content": "后续使用中文。"}
        ],
        conversation_summary="较早对话摘要",
        recent_messages=[
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": "上一轮回答"},
        ],
    )


def test_context_builder_01_assembles_all_sections_in_stable_order():
    context = _complete_context()

    assert list(context) == [
        "schema_version",
        "data_classification",
        "task_memory",
        "conversation_summary",
        "conversation_history",
        "followup_question",
        "existing_report",
        "evidence",
        "access_limitations",
    ]
    assert context["schema_version"] == "research-followup-context-v4"
    assert context["data_classification"] == "untrusted_research_data"
    assert context["followup_question"] == "当前问题"


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("task_memory", {"task_memory": []}),
        ("conversation_summary", {"conversation_summary": ""}),
        ("conversation_history", {"recent_messages": []}),
    ],
)
def test_context_builder_02_to_04_empty_optional_conversation_sections(
    field,
    kwargs,
):
    context = build_followup_context("问题", "", **kwargs)

    assert context[field] in ([], "")
    assert context["evidence"] == []
    assert context["access_limitations"] == []


def test_context_builder_05_current_question_appears_once():
    context = build_followup_context(
        "重复问题",
        "",
        recent_messages=[
            {"role": "user", "content": "重复问题"},
            {"role": "assistant", "content": "上一轮回答"},
        ],
    )

    assert [item["content"] for item in context["conversation_history"]] == [
        "上一轮回答"
    ]
    assert json.dumps(context, ensure_ascii=False).count("重复问题") == 1


def test_context_builder_06_pending_and_failed_assistant_are_excluded():
    context = build_followup_context(
        "问题",
        "",
        recent_messages=[
            {"role": "assistant", "content": "pending", "status": "pending"},
            {"role": "assistant", "content": "failed", "status": "failed"},
            {"role": "assistant", "content": "completed", "status": "completed"},
        ],
    )

    assert context["conversation_history"] == [
        {"role": "assistant", "content": "completed"}
    ]


def test_context_builder_07_task_contexts_remain_isolated():
    task_a = build_followup_context(
        "A问题",
        "A报告",
        task_memory=[{"id": 1, "category": "task_focus", "content": "A私有"}],
    )
    task_b = build_followup_context(
        "B问题",
        "B报告",
        task_memory=[{"id": 2, "category": "task_focus", "content": "B私有"}],
    )

    assert "A私有" not in json.dumps(task_b, ensure_ascii=False)
    assert "B私有" not in json.dumps(task_a, ensure_ascii=False)


def test_context_builder_08_memory_and_summary_are_independent_fields():
    context = _complete_context()

    assert context["task_memory"][0]["content"] == "后续使用中文。"
    assert context["conversation_summary"] == "较早对话摘要"
    assert "较早对话摘要" not in json.dumps(
        context["task_memory"], ensure_ascii=False
    )


def test_context_builder_09_report_and_evidence_are_independent_fields():
    context = _complete_context()

    assert context["existing_report"] == "已有报告"
    assert context["evidence"][0]["excerpt"] == "Evidence text."
    assert "Evidence text." not in context["existing_report"]


def test_context_builder_10_evidence_metadata_is_preserved():
    evidence = _complete_context()["evidence"][0]

    assert evidence == {
        "paper_title": "Paper A",
        "physical_page": 3,
        "chunk_id": 8,
        "excerpt": "Evidence text.",
    }


def test_context_builder_11_access_limitations_are_preserved():
    limitation = _complete_context()["access_limitations"][0]

    assert limitation == {
        "paper_id": 2,
        "paper_title": "Paper B",
        "url": "https://example.com/paper-b",
        "reason": "全文不可用",
    }


def test_context_builder_12_consumes_each_prepared_collection_once():
    class SinglePass:
        def __init__(self, values):
            self.values = values
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("prepared context was consumed twice")
            return iter(self.values)

    memory = SinglePass([])
    history = SinglePass([])
    evidence = SinglePass([])
    limitations = SinglePass([])

    build_followup_context(
        "问题",
        "报告",
        evidence,
        limitations,
        task_memory=memory,
        recent_messages=history,
    )

    assert [item.iterations for item in (memory, history, evidence, limitations)] == [
        1,
        1,
        1,
        1,
    ]


def test_context_builder_13_existing_prompt_entry_delegates_once(monkeypatch):
    calls = []

    def fake_builder(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "schema_version": "research-followup-context-v4",
            "data_classification": "untrusted_research_data",
            "followup_question": "问题",
        }

    monkeypatch.setattr(
        research_conversation_service,
        "build_followup_context",
        fake_builder,
    )

    prompt = research_conversation_service.build_research_followup_prompt(
        "问题",
        "报告",
        [],
        [],
    )

    assert len(calls) == 1
    assert json.loads(prompt.split("\n", 1)[1])["followup_question"] == "问题"


def test_context_builder_14_streaming_contract_is_unchanged(monkeypatch):
    updates = []
    monkeypatch.setattr(
        research_conversation_service,
        "get_research_message",
        lambda _message_id: {"task_id": 7},
    )
    monkeypatch.setattr(
        research_conversation_service,
        "update_research_message",
        lambda message_id, status, **kwargs: updates.append(
            {"message_id": message_id, "status": status, **kwargs}
        ),
    )
    monkeypatch.setattr(
        research_conversation_service,
        "prepare_research_followup_context",
        lambda *_args, **_kwargs: {
            "report_content": "原报告",
            "task_memory": [],
            "conversation_summary": "",
            "conversation_history": [],
            "evidence": [
                {
                    "paper_title": "Paper A",
                    "page_number": 2,
                    "chunk_id": 1,
                    "content": "Evidence.",
                }
            ],
            "access_limitations": [],
            "quality_gate": {"model_call_allowed": True},
        },
    )
    monkeypatch.setattr(
        research_conversation_service,
        "stream_deepseek",
        lambda *_args, **_kwargs: iter(["流式", "回答"]),
    )

    events = list(
        research_conversation_service.stream_research_followup(11, "问题")
    )

    assert [event for event, _payload in events] == [
        "status",
        "status",
        "delta",
        "delta",
        "done",
    ]
    assert updates[-1]["status"] == "completed"
    assert updates[-1]["content"] == "流式回答"


def test_context_builder_15_does_not_mutate_prepared_inputs():
    memory = [{"id": 1, "category": "preference", "content": "中文"}]
    history = [{"role": "user", "content": "旧问题"}]
    evidence = [
        {
            "paper_title": "Paper A",
            "page_number": 2,
            "chunk_id": 3,
            "content": "Evidence.",
        }
    ]
    limitations = [{"paper_title": "Paper B", "reason": "不可用"}]
    originals = json.loads(
        json.dumps([memory, history, evidence, limitations], ensure_ascii=False)
    )

    build_followup_context(
        "问题",
        "原报告",
        evidence,
        limitations,
        task_memory=memory,
        recent_messages=history,
    )

    assert [memory, history, evidence, limitations] == originals


def test_followup_context_without_report_keeps_evidence_and_partial_limitations(
    monkeypatch,
):
    monkeypatch.setattr(
        research_conversation_service,
        "get_research_task",
        lambda _task_id: {
            "id": 7,
            "status": "papers_selected",
            "selected_paper_ids": [1, 2],
        },
    )
    monkeypatch.setattr(
        research_conversation_service,
        "get_research_report",
        lambda _task_id: None,
    )
    monkeypatch.setattr(
        research_conversation_service,
        "update_task_memory_from_message",
        lambda *_args, **_kwargs: {"memories": [], "error": ""},
    )

    def retrieve(paper_id, _question, **kwargs):
        assert kwargs["top_k"] == 5
        if paper_id == 1:
            return {
                "evidence": [
                    {
                        "paper_id": 1,
                        "paper_title": "Prepared Paper",
                        "page_number": 4,
                        "chunk_id": 9,
                        "score": 0.9,
                        "content": "The prepared paper contains valid evidence.",
                    }
                ],
                "access_limitation": None,
            }
        return {
            "evidence": [],
            "access_limitation": {
                "paper_id": 2,
                "paper_title": "Unavailable Paper",
                "url": "https://example.com/unavailable",
                "reason": "全文不可访问",
            },
        }

    monkeypatch.setattr(
        research_evidence_service,
        "retrieve_report_paper_evidence",
        retrieve,
    )
    monkeypatch.setattr(
        research_conversation_service,
        "prepare_research_conversation_context",
        lambda *_args, **_kwargs: {
            "conversation_summary": "older summary",
            "recent_messages": [{"role": "user", "content": "older question"}],
            "summary_error": "",
        },
    )

    context = research_conversation_service.prepare_research_followup_context(
        7,
        "current question",
    )

    assert context["report_content"] == ""
    assert [item["paper_id"] for item in context["evidence"]] == [1]
    assert context["access_limitations"][0]["paper_id"] == 2
    assert context["conversation_summary"] == "older summary"

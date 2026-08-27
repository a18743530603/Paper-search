import json

import pytest

from my_agent_project import db, research_conversation_service, task_memory_service
from my_agent_project.schemas import PaperCandidate


def _setup_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "metadata.db")
    db.init_db()


def _create_task() -> int:
    return db.create_research_task(
        question="task memory research",
        max_results=5,
        plan_goal="Study task memory",
        plan_queries=["task memory"],
        plan_rationale="Test",
        plan_model="deepseek-test",
        planner_status="planned",
        planner_error="",
        status="awaiting_confirmation",
    )


def _insert_user_message(task_id: int, content: str) -> int:
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_messages (task_id, role, phase, content)
            VALUES (?, 'user', 'followup', ?)
            """,
            (task_id, content),
        )
    return int(cursor.lastrowid)


def _extract(task_id: int, content: str) -> dict:
    message_id = _insert_user_message(task_id, content)
    return task_memory_service.update_task_memory_from_message(
        task_id,
        message_id,
        content,
    )


def _mock_json(monkeypatch, payload):
    monkeypatch.setattr(
        task_memory_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (payload, "deepseek-test"),
    )


def _add_memory(monkeypatch, task_id: int, category: str, content: str) -> dict:
    _mock_json(
        monkeypatch,
        {
            "operations": [
                {"action": "add", "category": category, "content": content}
            ]
        },
    )
    return _extract(task_id, f"以后请记住：{content}")


def _create_report_ready_task() -> tuple[int, int]:
    paper = PaperCandidate(
        title="Task Memory Paper",
        authors="Test Author",
        summary="Task memory test",
        published="2026-08-26",
        source="arxiv",
        publisher="arXiv",
        doi=None,
        page_url="https://arxiv.org/abs/2608.12345",
        pdf_url="https://arxiv.org/pdf/2608.12345",
        status="available",
    )
    paper_id = db.insert_papers("memory", [paper])[0]
    task_id = _create_task()
    assert db.start_research_task(task_id)
    db.save_research_task_results(task_id, [paper_id])
    assert db.select_research_task_papers(task_id, [paper_id])
    db.start_research_report(task_id)
    db.update_research_report(
        task_id,
        "completed",
        content="Original evidence-grounded report.",
        model="deepseek-test",
    )
    return task_id, paper_id


def test_memory_01_one_turn_detail_request_is_noop(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    monkeypatch.setattr(
        task_memory_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("temporary request must not call extractor")
        ),
    )

    result = _extract(task_id, "这次详细一点。")

    assert result["status"] == "noop"
    assert db.list_research_task_memories(task_id) == []


def test_memory_02_persistent_detail_request_is_added(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _mock_json(
        monkeypatch,
        {
            "operations": [
                {
                    "action": "add",
                    "category": "preference",
                    "content": "后续回答保持详细。",
                }
            ]
        },
    )

    result = _extract(task_id, "后面都详细一点。")

    assert result["status"] == "updated"
    assert result["memories"][0]["content"] == "后续回答保持详细。"


def test_memory_03_language_memory_survives_next_turn(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _add_memory(monkeypatch, task_id, "preference", "后续回答使用英文。")
    monkeypatch.setattr(
        task_memory_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary question should use existing memory without extraction")
        ),
    )

    result = _extract(task_id, "Paper B 有什么限制？")

    assert result["status"] == "noop"
    assert result["memories"][0]["content"] == "后续回答使用英文。"


def test_memory_04_language_correction_updates_existing_memory(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    added = _add_memory(monkeypatch, task_id, "preference", "后续回答使用英文。")
    memory_id = added["memories"][0]["id"]
    _mock_json(
        monkeypatch,
        {
            "operations": [
                {
                    "action": "update",
                    "memory_id": memory_id,
                    "category": "preference",
                    "content": "后续回答使用中文。",
                }
            ]
        },
    )

    result = _extract(task_id, "还是用中文吧。")

    assert len(result["memories"]) == 1
    assert result["memories"][0]["id"] == memory_id
    assert result["memories"][0]["content"] == "后续回答使用中文。"


def test_memory_05_explicit_cancel_removes_memory(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    added = _add_memory(
        monkeypatch,
        task_id,
        "persistent_instruction",
        "后续回答保持简短。",
    )
    memory_id = added["memories"][0]["id"]
    _mock_json(
        monkeypatch,
        {"operations": [{"action": "remove", "memory_id": memory_id}]},
    )

    result = _extract(task_id, "不用再保持简短。")

    assert result["memories"] == []
    assert db.get_research_task_memory(memory_id)["status"] == "removed"


def test_memory_06_ordinary_paper_question_is_noop(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    monkeypatch.setattr(
        task_memory_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary question must not call extractor")
        ),
    )

    result = _extract(task_id, "Paper A 的准确率是多少？")

    assert result["status"] == "noop"
    assert result["memories"] == []


def test_memory_07_paper_fact_is_rejected_by_validator(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _mock_json(
        monkeypatch,
        {
            "operations": [
                {
                    "action": "add",
                    "category": "task_decision",
                    "content": "Paper A 的准确率为 92.1%。",
                }
            ]
        },
    )

    result = _extract(task_id, "以后记住 Paper A 的准确率为 92.1%。")

    assert result["status"] == "failed"
    assert result["memories"] == []
    assert "论文事实" in db.get_research_task(task_id)["task_memory_error"]


def test_memory_08_task_focus_can_be_saved(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _mock_json(
        monkeypatch,
        {
            "operations": [
                {
                    "action": "add",
                    "category": "task_focus",
                    "content": "当前持续关注 Paper B 性能优势的原因。",
                }
            ]
        },
    )

    result = _extract(task_id, "接下来主要搞清楚 Paper B 为什么效果更好。")

    assert result["memories"][0]["category"] == "task_focus"


def test_memory_09_tasks_are_isolated(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_a = _create_task()
    task_b = _create_task()
    _add_memory(monkeypatch, task_a, "preference", "后续回答使用中文。")

    assert len(db.list_research_task_memories(task_a)) == 1
    assert db.list_research_task_memories(task_b) == []


def test_memory_10_persists_across_reload(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    added = _add_memory(monkeypatch, task_id, "preference", "后续回答使用中文。")

    reloaded = db.list_research_task_memories(task_id)

    assert reloaded[0]["id"] == added["memories"][0]["id"]
    assert reloaded[0]["content"] == "后续回答使用中文。"


def test_memory_11_extractor_failure_does_not_break_streaming(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id, paper_id = _create_report_ready_task()
    original_report = db.get_research_report(task_id)["content"]
    created = db.create_research_followup_messages(
        task_id,
        "后面都用中文，并继续比较。",
    )
    monkeypatch.setattr(
        task_memory_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("memory extractor unavailable")
        ),
    )
    monkeypatch.setattr(
        research_conversation_service,
        "retrieve_report_paper_evidence",
        lambda *_args, **_kwargs: {
            "evidence": [
                {
                    "paper_id": paper_id,
                    "paper_title": "Task Memory Paper",
                    "page_number": 4,
                    "chunk_id": 3,
                    "score": 0.9,
                    "content": "The evidence supports the comparison.",
                }
            ],
            "access_limitation": None,
        },
    )
    monkeypatch.setattr(
        research_conversation_service,
        "stream_deepseek",
        lambda *_args, **_kwargs: iter(["真实", "流式回答"]),
    )

    events = list(
        research_conversation_service.stream_research_followup(
            created["message_id"],
            created["question"],
            current_user_message_id=created["user_message_id"],
        )
    )

    assert events[-1][0] == "done"
    assert db.get_research_message(created["message_id"])["content"] == "真实流式回答"
    assert "memory extractor unavailable" in db.get_research_task(task_id)[
        "task_memory_error"
    ]
    assert db.get_research_report(task_id)["content"] == original_report


def test_memory_12_invalid_json_contract_does_not_modify_memory(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _mock_json(monkeypatch, {"unexpected": []})

    result = _extract(task_id, "以后都用中文。")

    assert result["status"] == "failed"
    assert db.list_research_task_memories(task_id) == []


@pytest.mark.parametrize("action", ["update", "remove"])
def test_memory_13_cross_task_mutation_is_rejected(
    tmp_path,
    monkeypatch,
    action,
):
    _setup_database(tmp_path, monkeypatch)
    task_a = _create_task()
    task_b = _create_task()
    added = _add_memory(monkeypatch, task_a, "preference", "后续回答使用英文。")
    memory_id = added["memories"][0]["id"]
    operation = {"action": action, "memory_id": memory_id}
    if action == "update":
        operation.update(
            {"category": "preference", "content": "后续回答使用中文。"}
        )
        message = "还是用中文吧。"
    else:
        message = "不用再保持英文。"
    _mock_json(monkeypatch, {"operations": [operation]})

    result = _extract(task_b, message)

    assert result["status"] == "failed"
    assert db.get_research_task_memory(memory_id)["status"] == "active"
    assert db.list_research_task_memories(task_b) == []


def test_memory_14_duplicate_add_is_noop(tmp_path, monkeypatch):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _add_memory(monkeypatch, task_id, "preference", "后续回答使用中文。")
    _mock_json(
        monkeypatch,
        {
            "operations": [
                {
                    "action": "add",
                    "category": "preference",
                    "content": "后续回答使用中文。",
                }
            ]
        },
    )

    result = _extract(task_id, "以后继续保持中文回答。")

    assert result["status"] == "noop"
    assert len(db.list_research_task_memories(task_id)) == 1


def test_memory_15_summary_is_not_an_extractor_input_or_authority(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    _add_memory(monkeypatch, task_id, "preference", "后续回答使用中文。")
    assert db.update_research_conversation_summary(
        task_id,
        "较早摘要声称后续使用英文。",
        0,
        expected_previous_message_id=0,
    )
    captured = []

    def fake_json(_system_prompt, user_prompt, **_kwargs):
        captured.append(json.loads(user_prompt.split("\n", 1)[1]))
        return {"operations": []}, "deepseek-test"

    monkeypatch.setattr(task_memory_service, "ask_deepseek_json", fake_json)

    result = _extract(task_id, "后面继续保持中文回答。")

    assert result["memories"][0]["content"] == "后续回答使用中文。"
    assert "conversation_summary" not in captured[0]
    assert "较早摘要" not in json.dumps(captured[0], ensure_ascii=False)


def test_memory_16_current_temporary_intent_overrides_without_persisting(
    tmp_path,
    monkeypatch,
):
    _setup_database(tmp_path, monkeypatch)
    task_id = _create_task()
    added = _add_memory(monkeypatch, task_id, "preference", "后续回答使用英文。")
    monkeypatch.setattr(
        task_memory_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("one-turn override must not call extractor")
        ),
    )
    current_message = "这一次先用中文回答。"

    result = _extract(task_id, current_message)
    prompt = research_conversation_service.build_research_followup_prompt(
        current_message,
        "report",
        [
            {
                "paper_title": "Task Memory Paper",
                "page_number": 2,
                "chunk_id": 1,
                "content": "Evidence remains unchanged.",
            }
        ],
        [],
        task_memory=result["memories"],
    )
    payload = json.loads(prompt.split("\n", 1)[1])

    assert result["memories"][0]["id"] == added["memories"][0]["id"]
    assert result["memories"][0]["content"] == "后续回答使用英文。"
    assert payload["followup_question"] == current_message
    assert payload["task_memory"][0]["content"] == "后续回答使用英文。"
    assert payload["evidence"][0]["physical_page"] == 2
    assert "当前 User Message 中明确的要求" in (
        research_conversation_service.RESEARCH_FOLLOWUP_SYSTEM_PROMPT
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Paper B 使用 Dataset X", True),
        ("接下来重点比较各论文使用的数据集", False),
        ("后面都以 Paper B 作为基线进行比较", False),
    ],
)
def test_memory_17_paper_fact_guard_preserves_valid_task_intent(content, expected):
    assert task_memory_service._looks_like_paper_fact(content) is expected


def test_memory_18_validator_rejects_paper_fact_but_accepts_task_focus():
    with pytest.raises(ValueError, match="论文事实"):
        task_memory_service.validate_task_memory_operations(
            {
                "operations": [
                    {
                        "action": "add",
                        "category": "task_decision",
                        "content": "Paper B 使用 Dataset X",
                    }
                ]
            },
            task_id=1,
            current_user_message="以后记住 Paper B 使用 Dataset X。",
            active_memories=[],
        )

    assert task_memory_service.validate_task_memory_operations(
        {
            "operations": [
                {
                    "action": "add",
                    "category": "task_focus",
                    "content": "接下来重点比较各论文使用的数据集",
                }
            ]
        },
        task_id=1,
        current_user_message="接下来重点比较各论文使用的数据集。",
        active_memories=[],
    ) == [
        {
            "action": "add",
            "category": "task_focus",
            "content": "接下来重点比较各论文使用的数据集",
        }
    ]

    assert task_memory_service.validate_task_memory_operations(
        {
            "operations": [
                {
                    "action": "add",
                    "category": "task_decision",
                    "content": "后面都以 Paper B 作为基线进行比较",
                }
            ]
        },
        task_id=1,
        current_user_message="后面都以 Paper B 作为基线进行比较。",
        active_memories=[],
    ) == [
        {
            "action": "add",
            "category": "task_decision",
            "content": "后面都以 Paper B 作为基线进行比较",
        }
    ]

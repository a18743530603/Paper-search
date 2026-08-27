from typing import Any, Mapping, Sequence


FOLLOWUP_CONTEXT_SCHEMA_VERSION = "research-followup-context-v4"
FOLLOWUP_CONTEXT_DATA_CLASSIFICATION = "untrusted_research_data"
REPORT_CONTEXT_CHARACTER_LIMIT = 12000


def _integer_or_zero(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_followup_context(
    current_question: str,
    existing_report: str,
    evidence: Sequence[Mapping[str, Any]] = (),
    access_limitations: Sequence[Mapping[str, Any]] = (),
    *,
    task_memory: Sequence[Mapping[str, Any]] = (),
    conversation_summary: str = "",
    recent_messages: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Assemble already-prepared follow-up data without fetching or mutating it."""
    question = str(current_question or "")
    normalized_question = question.strip()
    conversation_history = []
    for item in recent_messages:
        status = str(item.get("status") or "completed")
        if status != "completed":
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role == "user" and content.strip() == normalized_question:
            continue
        conversation_history.append({"role": role, "content": content})

    return {
        "schema_version": FOLLOWUP_CONTEXT_SCHEMA_VERSION,
        "data_classification": FOLLOWUP_CONTEXT_DATA_CLASSIFICATION,
        "task_memory": [
            {
                "memory_id": _integer_or_zero(
                    item.get("id") or item.get("memory_id")
                ),
                "category": str(item.get("category") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in task_memory
        ],
        "conversation_summary": str(conversation_summary or ""),
        "conversation_history": conversation_history,
        "followup_question": question,
        "existing_report": str(existing_report or "")[
            :REPORT_CONTEXT_CHARACTER_LIMIT
        ],
        "evidence": [
            {
                "paper_title": str(item.get("paper_title") or "未知论文"),
                "physical_page": _integer_or_zero(item.get("page_number")),
                "chunk_id": item.get("chunk_id"),
                "excerpt": str(item.get("content") or ""),
            }
            for item in evidence
        ],
        "access_limitations": [dict(item) for item in access_limitations],
    }

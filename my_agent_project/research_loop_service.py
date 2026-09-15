import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .context_service import pack_research_context
from .db import get_paper
from .model_service import ask_deepseek_tool_call
from .research_chat_tool_contract import (
    COMPLETE_RESEARCH_ACTION_NAME,
    FIND_IN_PAPER_TOOL_NAME,
    GATHER_EVIDENCE_TOOL_NAME,
    INSPECT_TABLES_FIGURES_TOOL_NAME,
    LIST_SECTIONS_TOOL_NAME,
    READ_SECTION_TOOL_NAME,
    find_in_paper,
    gather_evidence,
    inspect_tables_figures,
    list_sections,
    read_section,
    research_agent_native_tool_schemas,
)
from .research_coverage_service import (
    RESEARCH_AGENT_SYSTEM_PROMPT,
    AnswerRequirement,
    plan_answer_requirements,
    reflect_requirement_coverage,
)


RESEARCH_TOOL_NAMES = frozenset(
    {
        GATHER_EVIDENCE_TOOL_NAME,
        LIST_SECTIONS_TOOL_NAME,
        READ_SECTION_TOOL_NAME,
        FIND_IN_PAPER_TOOL_NAME,
        INSPECT_TABLES_FIGURES_TOOL_NAME,
    }
)
COMPLETE_RESEARCH_ACTION = COMPLETE_RESEARCH_ACTION_NAME
MAX_RESEARCH_ACTIONS = 8
MAX_RESEARCH_RUNTIME_SECONDS = 90.0
MAX_STALLS_BEFORE_REPLAN = 2

COVERAGE_PROGRESS_TRANSITIONS = frozenset(
    {
        ("MISSING", "PARTIAL"),
        ("MISSING", "SUPPORTED"),
        ("MISSING", "NOT_AVAILABLE"),
        ("PARTIAL", "SUPPORTED"),
        ("PARTIAL", "NOT_AVAILABLE"),
        ("NOT_AVAILABLE", "PARTIAL"),
        ("NOT_AVAILABLE", "SUPPORTED"),
    }
)


class ResearchLoopContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchAction:
    target_requirement_id: str | None
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    tool_call_id: str | None = None
    request_message: dict[str, Any] | None = None
    assistant_message: dict[str, Any] | None = None
    decision_retry_count: int = 0
    rejected_tool_call_count: int = 0

    @property
    def is_completion(self) -> bool:
        return self.tool_name == COMPLETE_RESEARCH_ACTION

    def signature(self) -> str:
        return json.dumps(
            {"tool_name": self.tool_name, "arguments": self.arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": (
                "completion" if self.is_completion else "research_tool"
            ),
            "target_requirement_id": self.target_requirement_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "reason": self.reason,
            "tool_call_id": self.tool_call_id,
            "decision_protocol": "native_tool_call",
            "decision_retry_count": self.decision_retry_count,
            "rejected_tool_call_count": self.rejected_tool_call_count,
        }


@dataclass(slots=True)
class CoverageResearchResult:
    evidence: list[dict[str, Any]]
    access_limitations: list[dict[str, Any]]
    quality_gate: dict[str, Any]
    requirements: list[AnswerRequirement]
    diagnostics: dict[str, Any]

    def as_context_fields(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence,
            "access_limitations": self.access_limitations,
            "quality_gate": self.quality_gate,
            "query_routing": self.diagnostics.get("query_routing", {}),
            "retrieval_tool_calls": self.diagnostics.get(
                "retrieval_tool_calls", []
            ),
            "retrieval_diagnostics": self.diagnostics.get(
                "retrieval_diagnostics", []
            ),
            "research_loop": {
                "status": self.diagnostics.get("status", "COMPLETED"),
                "action_count": self.diagnostics.get("action_count", 0),
                "requirements": [item.as_dict() for item in self.requirements],
                "actions": self.diagnostics.get("actions", []),
                "completion_action": self.diagnostics.get("completion_action"),
                "exhaustion_reason": self.diagnostics.get("exhaustion_reason"),
                "elapsed_seconds": self.diagnostics.get("elapsed_seconds", 0.0),
                "max_actions": self.diagnostics.get(
                    "max_actions", MAX_RESEARCH_ACTIONS
                ),
                "max_runtime_seconds": self.diagnostics.get(
                    "max_runtime_seconds", MAX_RESEARCH_RUNTIME_SECONDS
                ),
                "stall_count": self.diagnostics.get("stall_count", 0),
                "max_stalls_before_replan": self.diagnostics.get(
                    "max_stalls_before_replan", MAX_STALLS_BEFORE_REPLAN
                ),
                "progress_events": self.diagnostics.get("progress_events", []),
                "decision_protocol": self.diagnostics.get(
                    "decision_protocol", "deepseek_native_tool_calling"
                ),
                "native_tool_trace": self.diagnostics.get(
                    "native_tool_trace", []
                ),
            },
        }


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchLoopContractError(f"{field}必须为正整数") from exc
    if parsed <= 0:
        raise ResearchLoopContractError(f"{field}必须为正整数")
    return parsed


def _nonempty_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ResearchLoopContractError(f"{field}不能为空")
    return text


def _selected_paper_summaries(paper_ids: Sequence[int]) -> list[dict[str, Any]]:
    summaries = []
    for paper_id in paper_ids:
        row = get_paper(int(paper_id))
        summaries.append(
            {
                "paper_id": int(paper_id),
                "paper_title": str(row["title"] or "") if row is not None else "",
            }
        )
    return summaries


def _assistant_tool_calls(
    assistant_message: Mapping[str, Any],
) -> list[Any]:
    if str(assistant_message.get("role") or "") != "assistant":
        raise ResearchLoopContractError(
            "原生Tool Calling必须返回assistant消息"
        )
    raw_tool_calls = assistant_message.get("tool_calls")
    if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
        raise ResearchLoopContractError(
            "Research Decision必须显式返回一个Tool Call"
        )
    return raw_tool_calls


def _multiple_tool_call_correction_messages(
    assistant_message: Mapping[str, Any],
    raw_tool_calls: Sequence[Any],
) -> list[dict[str, Any]]:
    correction_messages = [dict(assistant_message)]
    seen_tool_call_ids: set[str] = set()
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, Mapping):
            raise ResearchLoopContractError("多Tool Call纠正所需结构无效")
        tool_call_id = _nonempty_text(
            raw_tool_call.get("id"),
            "tool_call.id",
        )
        if tool_call_id in seen_tool_call_ids:
            raise ResearchLoopContractError("Tool Call ID不得重复")
        seen_tool_call_ids.add(tool_call_id)
        correction_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(
                    {
                        "status": "contract_rejected",
                        "reason": (
                            "Sequential Research Loop每轮只允许一个Action；"
                            "该调用未执行。"
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    correction_messages.append(
        {
            "role": "user",
            "content": (
                "The previous decision violated the sequential-action contract: "
                "it requested multiple function calls, so none were executed. "
                "Correct the decision now by choosing exactly one of the supplied "
                "functions. Do not return multiple tool calls."
            ),
        }
    )
    return correction_messages


def decide_next_research_action(
    *,
    question: str,
    selected_papers: Sequence[Mapping[str, Any]],
    requirements: Sequence[AnswerRequirement],
    accumulated_evidence: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    previous_actions: Sequence[ResearchAction],
    no_progress_context: Mapping[str, Any] | None = None,
    agent_messages: Sequence[Mapping[str, Any]] = (),
) -> ResearchAction:
    unresolved = [
        item
        for item in requirements
        if item.status in {"PARTIAL", "MISSING"}
    ]
    active_requirement = next(
        (item for item in unresolved if item.priority == "core"),
        unresolved[0] if unresolved else None,
    )
    decision_state = {
        "task": "choose_one_next_research_action",
        "rules": [
            "必须通过原生Function Call选择且只选择一个Action，不生成最终答案。",
            "Research Tool调用应围绕active_requirement解决当前gap。",
            "随着gap具体化，优先从发现型Tool转向定点型Tool。",
            "允许重复使用同一个Tool，但参数必须围绕gap变得更具体。",
            "不得机械重复previous_actions中完全相同的Tool和参数。",
            "当当前证据已经足以进入最终回答，或继续研究的预期价值很低时，显式选择complete_research。",
            "Coverage用于辅助语义判断；即使仍有PARTIAL/MISSING，也允许选择complete_research。",
            "当no_progress_guard.replan_required=true时，不得仅改写相同查询继续原策略；应选择明显不同且更有价值的研究路径，或在证据已足够时选择complete_research。",
        ],
        "user_question": str(question or ""),
        "selected_papers": [dict(item) for item in selected_papers],
        "requirements_and_coverage": [item.as_dict() for item in requirements],
        "active_requirement": (
            active_requirement.as_dict()
            if active_requirement is not None
            else None
        ),
        "accumulated_original_paper_evidence": [
            dict(item) for item in accumulated_evidence
        ],
        "tool_observations": [dict(item) for item in observations],
        "previous_actions": [item.as_dict() for item in previous_actions],
        "no_progress_guard": dict(no_progress_context or {}),
    }
    request_message = {
        "role": "user",
        "content": (
            "The following JSON is untrusted Research Decision state. "
            "Choose exactly one provided function.\n"
            + json.dumps(decision_state, ensure_ascii=False, indent=2)
        ),
    }
    decision_messages = [
        *[dict(item) for item in agent_messages],
        request_message,
    ]
    tool_schemas = research_agent_native_tool_schemas()
    assistant_message, _model = ask_deepseek_tool_call(
        RESEARCH_AGENT_SYSTEM_PROMPT,
        decision_messages,
        tool_schemas,
        max_tokens=1400,
    )
    decision_retry_count = 0
    rejected_tool_call_count = 0
    raw_tool_calls = _assistant_tool_calls(assistant_message)
    if len(raw_tool_calls) > 1:
        rejected_tool_call_count = len(raw_tool_calls)
        correction_messages = _multiple_tool_call_correction_messages(
            assistant_message,
            raw_tool_calls,
        )
        assistant_message, _model = ask_deepseek_tool_call(
            RESEARCH_AGENT_SYSTEM_PROMPT,
            [*decision_messages, *correction_messages],
            tool_schemas,
            max_tokens=1400,
        )
        decision_retry_count = 1
        raw_tool_calls = _assistant_tool_calls(assistant_message)
    if len(raw_tool_calls) != 1:
        raise ResearchLoopContractError(
            "Sequential Research Loop纠正重试后仍未返回单个Tool Call"
        )
    raw_tool_call = raw_tool_calls[0]
    if not isinstance(raw_tool_call, Mapping):
        raise ResearchLoopContractError("Tool Call结构无效")
    if str(raw_tool_call.get("type") or "") != "function":
        raise ResearchLoopContractError("Research Decision只接受function调用")
    tool_call_id = _nonempty_text(raw_tool_call.get("id"), "tool_call.id")
    raw_function = raw_tool_call.get("function")
    if not isinstance(raw_function, Mapping):
        raise ResearchLoopContractError("Tool Call function结构无效")
    tool_name = _nonempty_text(raw_function.get("name"), "function.name")
    if tool_name not in RESEARCH_TOOL_NAMES | {COMPLETE_RESEARCH_ACTION}:
        raise ResearchLoopContractError("Research Action名称无效")
    encoded_arguments = raw_function.get("arguments")
    if not isinstance(encoded_arguments, str):
        raise ResearchLoopContractError("function.arguments必须是JSON字符串")
    try:
        raw_arguments = json.loads(encoded_arguments)
    except json.JSONDecodeError as exc:
        raise ResearchLoopContractError(
            "function.arguments不是有效JSON"
        ) from exc
    if not isinstance(raw_arguments, Mapping):
        raise ResearchLoopContractError("function.arguments必须解析为对象")
    raw_content = assistant_message.get("content")
    reason = (
        str(raw_content).strip()
        if isinstance(raw_content, str) and raw_content.strip()
        else f"Agent通过原生Tool Calling选择{tool_name}"
    )
    if tool_name == COMPLETE_RESEARCH_ACTION:
        if raw_arguments:
            raise ResearchLoopContractError("complete_research不得包含arguments")
        return ResearchAction(
            target_requirement_id=None,
            tool_name=tool_name,
            arguments={},
            reason=reason,
            tool_call_id=tool_call_id,
            request_message=request_message,
            assistant_message=dict(assistant_message),
            decision_retry_count=decision_retry_count,
            rejected_tool_call_count=rejected_tool_call_count,
        )

    if active_requirement is None:
        raise ResearchLoopContractError("下一动作必须针对未解决Requirement")
    arguments = _validate_action_arguments(
        tool_name,
        raw_arguments,
        selected_paper_ids={int(item["paper_id"]) for item in selected_papers},
        accumulated_evidence=accumulated_evidence,
    )
    action = ResearchAction(
        target_requirement_id=active_requirement.id,
        tool_name=tool_name,
        arguments=arguments,
        reason=reason,
        tool_call_id=tool_call_id,
        request_message=request_message,
        assistant_message=dict(assistant_message),
        decision_retry_count=decision_retry_count,
        rejected_tool_call_count=rejected_tool_call_count,
    )
    if action.signature() in {item.signature() for item in previous_actions}:
        raise ResearchLoopContractError("Research Action不得机械重复")
    return action


def _validate_action_arguments(
    tool_name: str,
    raw: Mapping[str, Any],
    *,
    selected_paper_ids: set[int],
    accumulated_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    allowed_keys = {
        GATHER_EVIDENCE_TOOL_NAME: {"question", "paper_ids"},
        LIST_SECTIONS_TOOL_NAME: {"paper_id"},
        READ_SECTION_TOOL_NAME: {"paper_id", "section_path"},
        FIND_IN_PAPER_TOOL_NAME: {"paper_id", "query"},
        INSPECT_TABLES_FIGURES_TOOL_NAME: {"paper_id", "chunk_id"},
    }[tool_name]
    unexpected_keys = set(raw) - allowed_keys
    if unexpected_keys:
        raise ResearchLoopContractError(
            "Tool arguments包含未声明字段："
            + ", ".join(sorted(str(item) for item in unexpected_keys))
        )

    def selected_paper(value: object) -> int:
        paper_id = _positive_int(value, "paper_id")
        if paper_id not in selected_paper_ids:
            raise ResearchLoopContractError("Tool只能访问当前任务已选择论文")
        return paper_id

    if tool_name == GATHER_EVIDENCE_TOOL_NAME:
        question = _nonempty_text(raw.get("question"), "question")
        raw_paper_ids = raw.get("paper_ids")
        if not isinstance(raw_paper_ids, list) or not raw_paper_ids:
            raise ResearchLoopContractError("gather_evidence.paper_ids必须是非空数组")
        paper_ids = list(
            dict.fromkeys(selected_paper(item) for item in raw_paper_ids)
        )
        return {"question": question, "paper_ids": paper_ids}
    if tool_name == LIST_SECTIONS_TOOL_NAME:
        return {"paper_id": selected_paper(raw.get("paper_id"))}
    if tool_name == READ_SECTION_TOOL_NAME:
        return {
            "paper_id": selected_paper(raw.get("paper_id")),
            "section_path": _nonempty_text(
                raw.get("section_path"),
                "section_path",
            ),
        }
    if tool_name == FIND_IN_PAPER_TOOL_NAME:
        return {
            "paper_id": selected_paper(raw.get("paper_id")),
            "query": _nonempty_text(raw.get("query"), "query"),
        }
    paper_id = selected_paper(raw.get("paper_id"))
    chunk_id = _positive_int(raw.get("chunk_id"), "chunk_id")
    known_chunks = {
        (int(item.get("paper_id") or 0), int(item.get("chunk_id") or 0))
        for item in accumulated_evidence
    }
    if (paper_id, chunk_id) not in known_chunks:
        raise ResearchLoopContractError(
            "inspect_tables_figures只能查看已由当前研究发现的Chunk"
        )
    return {"paper_id": paper_id, "chunk_id": chunk_id}


def _execute_action(action: ResearchAction) -> dict[str, Any]:
    if action.is_completion:
        raise ResearchLoopContractError("complete_research不得由Tool dispatcher执行")
    if action.tool_name == GATHER_EVIDENCE_TOOL_NAME:
        observation = gather_evidence(**action.arguments)
    elif action.tool_name == LIST_SECTIONS_TOOL_NAME:
        observation = list_sections(**action.arguments)
    elif action.tool_name == READ_SECTION_TOOL_NAME:
        observation = read_section(**action.arguments)
    elif action.tool_name == FIND_IN_PAPER_TOOL_NAME:
        observation = find_in_paper(**action.arguments)
    else:
        observation = inspect_tables_figures(**action.arguments)
    payload = observation.as_dict()
    payload["execution_status"] = "succeeded"
    return payload


def _tool_observation_message(
    action: ResearchAction,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    tool_call_id = _nonempty_text(action.tool_call_id, "tool_call_id")
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(
            dict(observation),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
    }


def _evidence_id(item: Mapping[str, Any]) -> str:
    existing = str(item.get("evidence_id") or "").strip()
    if existing:
        return existing
    parent_chunk_id = item.get("parent_chunk_id")
    if parent_chunk_id is not None:
        return f"paper_parent_chunk:{int(parent_chunk_id)}"
    return f"paper_chunk:{int(item['chunk_id'])}"


def _normalize_tool_evidence(item: Mapping[str, Any]) -> dict[str, Any] | None:
    if not str(item.get("content") or "").strip():
        return None
    paper_id = _positive_int(item.get("paper_id"), "evidence.paper_id")
    chunk_id = _positive_int(item.get("chunk_id"), "evidence.chunk_id")
    physical_page = item.get("page_number", item.get("physical_page"))
    page_number = _positive_int(physical_page, "evidence.physical_page")
    normalized = dict(item)
    normalized.update(
        {
            "evidence_id": _evidence_id(item),
            "paper_id": paper_id,
            "chunk_id": chunk_id,
            "page_number": page_number,
            "paper_title": str(item.get("paper_title") or "未知论文"),
            "content": str(item.get("content") or ""),
        }
    )
    normalized.pop("physical_page", None)
    return normalized


def _observation_evidence(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_evidence: list[Mapping[str, Any]] = []
    evidence = observation.get("evidence")
    if isinstance(evidence, list):
        raw_evidence.extend(item for item in evidence if isinstance(item, Mapping))
    text_evidence = observation.get("text_evidence")
    if isinstance(text_evidence, Mapping):
        visual_item = dict(text_evidence)
        if observation.get("visual_id"):
            visual_item["visual_id"] = str(observation["visual_id"])
        raw_evidence.append(visual_item)
    normalized = []
    for item in raw_evidence:
        try:
            prepared = _normalize_tool_evidence(item)
        except (KeyError, TypeError, ValueError):
            continue
        if prepared is not None:
            normalized.append(prepared)
    return normalized


def _merge_evidence(
    accumulated: Sequence[Mapping[str, Any]],
    new_evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged = [dict(item) for item in accumulated]
    positions = {
        str(item.get("evidence_id") or ""): index
        for index, item in enumerate(merged)
    }
    for item in new_evidence:
        copied = dict(item)
        evidence_id = str(copied["evidence_id"])
        if evidence_id in positions:
            existing = merged[positions[evidence_id]]
            if copied.get("visual_id"):
                existing["visual_id"] = copied["visual_id"]
            continue
        positions[evidence_id] = len(merged)
        merged.append(copied)
    return merged


def _compact_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in observation.items()
        if key not in {"evidence", "text_evidence"}
    }
    compact["evidence_ids"] = [
        item["evidence_id"] for item in _observation_evidence(observation)
    ]
    return compact


def _merge_access_limitations(
    accumulated: Sequence[Mapping[str, Any]],
    observation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    merged = [dict(item) for item in accumulated]
    seen = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        for item in merged
    }
    raw = observation.get("access_limitations")
    if not isinstance(raw, list):
        return merged
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        copied = dict(item)
        signature = json.dumps(
            copied,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if signature not in seen:
            merged.append(copied)
            seen.add(signature)
    return merged


def _coverage_improvements(
    before: Sequence[AnswerRequirement],
    after: Sequence[AnswerRequirement],
) -> list[str]:
    before_by_id = {item.id: item.status for item in before}
    return [
        item.id
        for item in after
        if (before_by_id.get(item.id), item.status)
        in COVERAGE_PROGRESS_TRANSITIONS
    ]


def _no_progress_context(
    *,
    stall_count: int,
    progress_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    replan_required = stall_count >= MAX_STALLS_BEFORE_REPLAN
    context: dict[str, Any] = {
        "stall_count": stall_count,
        "max_stalls_before_replan": MAX_STALLS_BEFORE_REPLAN,
        "replan_required": replan_required,
    }
    if replan_required:
        context["instruction"] = (
            "最近连续多轮研究没有带来新的有效Original Paper Evidence，"
            "也没有改善Requirement Coverage。不要继续使用当前研究策略或仅改写"
            "相同查询。请根据unresolved requirement、gap、已有observations和"
            "previous actions，选择明显不同且更有价值的研究路径；如果认为现有"
            "证据已经足够，可以选择complete_research。"
        )
        context["recent_no_progress_actions"] = [
            {
                "action_index": item.get("action_index"),
                "tool_name": item.get("tool_name"),
            }
            for item in progress_events[-stall_count:]
            if not item.get("progress")
        ]
    return context


def run_coverage_research_loop(
    question: str,
    paper_ids: Sequence[int],
    *,
    max_actions: int = MAX_RESEARCH_ACTIONS,
    max_runtime: float = MAX_RESEARCH_RUNTIME_SECONDS,
) -> CoverageResearchResult:
    started_at = time.monotonic()
    if max_actions <= 0:
        raise ValueError("max_actions必须为正整数")
    if max_runtime <= 0:
        raise ValueError("max_runtime必须为正数")
    selected_ids = list(dict.fromkeys(int(item) for item in paper_ids))
    if not selected_ids or any(item <= 0 for item in selected_ids):
        raise ValueError("paper_ids必须包含至少一个正整数")

    selected_papers = _selected_paper_summaries(selected_ids)
    requirements = plan_answer_requirements(question)
    accumulated_evidence: list[dict[str, Any]] = []
    access_limitations: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    actions: list[ResearchAction] = []
    targeted_attempts = {item.id: 0 for item in requirements}
    retrieval_tool_calls: list[dict[str, Any]] = []
    retrieval_diagnostics: list[dict[str, Any]] = []
    query_routing: dict[str, Any] = {}
    loop_status = "EXHAUSTED"
    exhaustion_reason: str | None = None
    completion_action: ResearchAction | None = None
    stall_count = 0
    progress_events: list[dict[str, Any]] = []
    agent_messages: list[dict[str, Any]] = []
    native_tool_trace: list[dict[str, Any]] = []

    def runtime_exhausted() -> bool:
        return time.monotonic() - started_at >= max_runtime

    while True:
        if len(actions) >= max_actions:
            exhaustion_reason = "max_actions"
            break
        if runtime_exhausted():
            exhaustion_reason = "max_runtime"
            break
        action = decide_next_research_action(
            question=question,
            selected_papers=selected_papers,
            requirements=requirements,
            accumulated_evidence=accumulated_evidence,
            observations=observations,
            previous_actions=actions,
            no_progress_context=_no_progress_context(
                stall_count=stall_count,
                progress_events=progress_events,
            ),
            agent_messages=agent_messages,
        )
        if action.request_message is not None:
            agent_messages.append(dict(action.request_message))
        if action.assistant_message is not None:
            agent_messages.append(dict(action.assistant_message))
        native_tool_trace.append(
            {
                "role": "assistant",
                "tool_call_id": action.tool_call_id,
                "function_name": action.tool_name,
                "arguments": dict(action.arguments),
                "decision_retry_count": action.decision_retry_count,
                "rejected_tool_call_count": action.rejected_tool_call_count,
            }
        )
        if runtime_exhausted():
            exhaustion_reason = "max_runtime"
            break
        if action.is_completion:
            completion_action = action
            loop_status = "COMPLETED"
            break

        previous_evidence_ids = {
            str(item.get("evidence_id") or "")
            for item in accumulated_evidence
            if str(item.get("evidence_id") or "")
        }
        previous_requirements = list(requirements)
        observation = _execute_action(action)
        tool_message = _tool_observation_message(action, observation)
        agent_messages.append(tool_message)
        new_evidence = _observation_evidence(observation)
        accumulated_evidence = _merge_evidence(
            accumulated_evidence,
            new_evidence,
        )
        context_pack = pack_research_context(accumulated_evidence)
        accumulated_evidence = [dict(item) for item in context_pack["evidence"]]
        current_evidence_ids = {
            str(item.get("evidence_id") or "")
            for item in accumulated_evidence
            if str(item.get("evidence_id") or "")
        }
        new_evidence_ids = sorted(current_evidence_ids - previous_evidence_ids)
        access_limitations = _merge_access_limitations(
            access_limitations,
            observation,
        )
        compact_observation = _compact_observation(observation)
        observations.append(compact_observation)
        native_tool_trace.append(
            {
                "role": "tool",
                "tool_call_id": action.tool_call_id,
                "function_name": action.tool_name,
                "observation_status": observation.get("status"),
                "evidence_ids": compact_observation["evidence_ids"],
                "serialized_content_length": len(tool_message["content"]),
            }
        )
        actions.append(action)
        targeted_attempts[str(action.target_requirement_id)] += 1

        diagnostics = observation.get("diagnostics")
        if action.tool_name == "gather_evidence" and isinstance(
            diagnostics, Mapping
        ):
            if isinstance(diagnostics.get("query_routing"), Mapping):
                query_routing = dict(diagnostics["query_routing"])
            tool_calls = diagnostics.get("retrieval_tool_calls")
            if isinstance(tool_calls, list):
                retrieval_tool_calls.extend(
                    dict(item) for item in tool_calls if isinstance(item, Mapping)
                )
            tool_diagnostics = diagnostics.get("retrieval_diagnostics")
            if isinstance(tool_diagnostics, list):
                retrieval_diagnostics.extend(
                    dict(item)
                    for item in tool_diagnostics
                    if isinstance(item, Mapping)
                )

        if runtime_exhausted():
            progress = bool(new_evidence_ids)
            stall_count = 0 if progress else stall_count + 1
            progress_events.append(
                {
                    "action_index": len(actions),
                    "tool_name": action.tool_name,
                    "new_evidence_ids": new_evidence_ids,
                    "new_evidence_count": len(new_evidence_ids),
                    "coverage_improved": False,
                    "coverage_improved_requirement_ids": [],
                    "coverage_reflection_completed": False,
                    "progress": progress,
                    "stall_count": stall_count,
                    "replan_triggered": (
                        stall_count >= MAX_STALLS_BEFORE_REPLAN
                    ),
                }
            )
            exhaustion_reason = "max_runtime"
            break

        requirements = reflect_requirement_coverage(
            question=question,
            requirements=requirements,
            accumulated_evidence=accumulated_evidence,
            observations=observations,
            targeted_attempts=targeted_attempts,
        )
        improved_requirement_ids = _coverage_improvements(
            previous_requirements,
            requirements,
        )
        progress = bool(new_evidence_ids or improved_requirement_ids)
        stall_count = 0 if progress else stall_count + 1
        progress_events.append(
            {
                "action_index": len(actions),
                "tool_name": action.tool_name,
                "new_evidence_ids": new_evidence_ids,
                "new_evidence_count": len(new_evidence_ids),
                "coverage_improved": bool(improved_requirement_ids),
                "coverage_improved_requirement_ids": improved_requirement_ids,
                "coverage_reflection_completed": True,
                "progress": progress,
                "stall_count": stall_count,
                "replan_triggered": (
                    stall_count >= MAX_STALLS_BEFORE_REPLAN
                ),
            }
        )
        if runtime_exhausted():
            exhaustion_reason = "max_runtime"
            break

    for requirement in requirements:
        if requirement.priority == "core" and requirement.status == "NOT_AVAILABLE":
            access_limitations.append(
                {
                    "paper_title": "当前所选论文",
                    "requirement_id": requirement.id,
                    "requirement": requirement.requirement,
                    "reason": requirement.gap,
                }
            )
        elif requirement.status in {"PARTIAL", "MISSING"}:
            access_limitations.append(
                {
                    "paper_title": "当前所选论文",
                    "requirement_id": requirement.id,
                    "requirement": requirement.requirement,
                    "coverage_status": requirement.status,
                    "reason": requirement.gap,
                    "next_research_need": requirement.next_research_need,
                    "research_loop_status": loop_status,
                    "research_loop_exhaustion_reason": exhaustion_reason,
                }
            )
    final_pack = pack_research_context(accumulated_evidence)
    elapsed_seconds = max(0.0, time.monotonic() - started_at)
    return CoverageResearchResult(
        evidence=[dict(item) for item in final_pack["evidence"]],
        access_limitations=access_limitations,
        quality_gate=dict(final_pack["quality_gate"]),
        requirements=list(requirements),
        diagnostics={
            "status": loop_status,
            "action_count": len(actions),
            "actions": [item.as_dict() for item in actions],
            "completion_action": (
                completion_action.as_dict() if completion_action is not None else None
            ),
            "exhaustion_reason": exhaustion_reason,
            "elapsed_seconds": elapsed_seconds,
            "max_actions": max_actions,
            "max_runtime_seconds": max_runtime,
            "stall_count": stall_count,
            "max_stalls_before_replan": MAX_STALLS_BEFORE_REPLAN,
            "progress_events": progress_events,
            "decision_protocol": "deepseek_native_tool_calling",
            "native_tool_trace": native_tool_trace,
            "agent_message_count": len(agent_messages),
            "observations": observations,
            "targeted_attempts": targeted_attempts,
            "query_routing": query_routing,
            "retrieval_tool_calls": retrieval_tool_calls,
            "retrieval_diagnostics": retrieval_diagnostics,
        },
    )

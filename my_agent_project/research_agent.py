from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .context_service import pack_research_context
from .model_service import (
    ModelConfigurationError,
    ModelRequestError,
    ask_deepseek_json,
)
from .schemas import PaperCandidate
from .research_report_service import (
    generate_report_content,
    prepare_report_paper,
    retrieve_report_paper_evidence,
    validate_research_report_citations,
)
from .research_tools import LITERATURE_SEARCH_TOOL_NAME, invoke_research_tool


SEARCH_MODE_STANDARD = "standard"
SEARCH_MODE_RESEARCH_AGENT = "research_agent"
PLANNER_STATUS_PLANNED = "planned"
PLANNER_STATUS_FALLBACK = "fallback"
MAX_PLANNED_QUERIES = 4

RESEARCH_PLANNER_SYSTEM_PROMPT = """You plan academic literature searches.
Return one JSON object with exactly these fields:
- goal: a concise restatement of the research goal;
- queries: 2 to 4 distinct English academic search queries;
- rationale: one concise sentence explaining the query coverage.

Keep named methods, datasets, model names, acronyms, and paper titles unchanged.
Do not answer the research question. Do not decide whether any paper should be
downloaded. Do not include Markdown or fields outside the JSON object.
"""


class ResearchPlanError(ValueError):
    pass


class ResearchSearchState(TypedDict, total=False):
    question: str
    max_results: int
    confirmed: bool
    plan_goal: str
    plan_queries: list[str]
    plan_rationale: str
    plan_model: str
    plan_status: str
    plan_error: str
    workflow_status: str
    raw_candidates: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    candidate_paper_ids: list[int]
    selected_paper_ids: list[int]
    tool_calls: list[dict[str, Any]]
    report_confirmed: bool
    report_prepare_index: int
    report_retrieve_index: int
    report_evidence: list[dict[str, Any]]
    report_context_evidence: list[dict[str, Any]]
    report_context_stats: dict[str, Any]
    report_context_quality: dict[str, Any]
    report_access_limitations: list[dict[str, Any]]
    report_tool_calls: list[dict[str, Any]]
    report_content: str
    report_model: str
    report_citation_validation: dict[str, Any]


@dataclass(frozen=True)
class ResearchSearchResult:
    goal: str
    queries: list[str]
    rationale: str
    model: str
    status: str
    error: str
    workflow_status: str
    candidates: list[PaperCandidate]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_research_plan(
    payload: dict[str, Any],
    question: str,
) -> tuple[str, list[str], str]:
    goal = _clean_text(payload.get("goal")) or question
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list):
        raise ResearchPlanError("检索计划缺少 queries 列表")

    queries: list[str] = []
    seen: set[str] = set()
    for value in raw_queries:
        query = _clean_text(value)
        key = query.casefold()
        if not query or len(query) > 240 or key in seen:
            continue
        queries.append(query)
        seen.add(key)
        if len(queries) == MAX_PLANNED_QUERIES:
            break

    if len(queries) < 2:
        raise ResearchPlanError("检索计划至少需要两个不同的有效查询")
    rationale = _clean_text(payload.get("rationale"))
    return goal, queries, rationale


def plan_research(state: ResearchSearchState) -> dict[str, Any]:
    if state.get("plan_queries") and state.get("plan_status"):
        return {}
    question = state["question"]
    try:
        payload, model = ask_deepseek_json(
            RESEARCH_PLANNER_SYSTEM_PROMPT,
            f"Research question: {question}",
            max_tokens=700,
        )
        goal, queries, rationale = normalize_research_plan(payload, question)
        return {
            "plan_goal": goal,
            "plan_queries": queries,
            "plan_rationale": rationale,
            "plan_model": model,
            "plan_status": PLANNER_STATUS_PLANNED,
            "plan_error": "",
        }
    except (ModelConfigurationError, ModelRequestError, ResearchPlanError) as exc:
        return {
            "plan_goal": question,
            "plan_queries": [question],
            "plan_rationale": "",
            "plan_model": "",
            "plan_status": PLANNER_STATUS_FALLBACK,
            "plan_error": str(exc)[:500],
        }


def route_after_plan(
    state: ResearchSearchState,
) -> Literal["await_confirmation", "multi_query_search", "single_query_search"]:
    if not state.get("confirmed", False):
        return "await_confirmation"
    if state.get("plan_status") == PLANNER_STATUS_PLANNED:
        return "multi_query_search"
    return "single_query_search"


def await_confirmation(_state: ResearchSearchState) -> dict[str, Any]:
    return {"workflow_status": "awaiting_confirmation"}


def request_plan_confirmation(state: ResearchSearchState) -> dict[str, Any]:
    decision = interrupt(
        {
            "kind": "plan_confirmation",
            "goal": state["plan_goal"],
            "queries": state["plan_queries"],
            "rationale": state.get("plan_rationale", ""),
            "planner_status": state["plan_status"],
            "planner_error": state.get("plan_error", ""),
        }
    )
    approved = bool(
        decision.get("approved", False)
        if isinstance(decision, dict)
        else decision
    )
    return {
        "confirmed": approved,
        "workflow_status": "running" if approved else "cancelled",
    }


def route_after_native_confirmation(
    state: ResearchSearchState,
) -> Literal["multi_query_search", "single_query_search", "cancel_research"]:
    if not state.get("confirmed", False):
        return "cancel_research"
    if state.get("plan_status") == PLANNER_STATUS_PLANNED:
        return "multi_query_search"
    return "single_query_search"


def cancel_research(_state: ResearchSearchState) -> dict[str, Any]:
    return {"workflow_status": "cancelled"}


def _candidate_dicts(
    queries: list[str],
    per_query_results: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for query in queries:
        tool_result = invoke_research_tool(
            LITERATURE_SEARCH_TOOL_NAME,
            {"query": query, "max_results": per_query_results},
        )
        candidates.extend(tool_result.get("candidates", []))
        tool_calls.append(
            {
                key: value
                for key, value in tool_result.items()
                if key != "candidates"
            }
        )
    return candidates, tool_calls


def multi_query_search(state: ResearchSearchState) -> dict[str, Any]:
    queries = state["plan_queries"]
    max_results = state["max_results"]
    per_query_results = max(2, (max_results + len(queries) - 1) // len(queries))
    candidates, tool_calls = _candidate_dicts(queries, per_query_results)
    return {"raw_candidates": candidates, "tool_calls": tool_calls}


def single_query_search(state: ResearchSearchState) -> dict[str, Any]:
    candidates, tool_calls = _candidate_dicts(
        [state["question"]],
        state["max_results"],
    )
    return {"raw_candidates": candidates, "tool_calls": tool_calls}


def _candidate_identifiers(candidate: dict[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    doi = _clean_text(candidate.get("doi")).casefold()
    page_url = _clean_text(candidate.get("page_url")).rstrip("/").casefold()
    title = _clean_text(candidate.get("title")).casefold()
    if doi:
        identifiers.add(f"doi:{doi}")
    if page_url:
        identifiers.add(f"url:{page_url}")
    if title:
        identifiers.add(f"title:{title}")
    return identifiers


def deduplicate_results(state: ResearchSearchState) -> dict[str, Any]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in state.get("raw_candidates", []):
        identifiers = _candidate_identifiers(candidate)
        if identifiers and identifiers & seen:
            continue
        unique.append(candidate)
        seen.update(identifiers)
        if len(unique) == state["max_results"]:
            break
    return {"candidates": unique, "workflow_status": "awaiting_selection"}


def request_paper_selection(state: ResearchSearchState) -> dict[str, Any]:
    decision = interrupt(
        {
            "kind": "paper_selection",
            "candidate_count": len(state.get("candidates", [])),
            "candidates": [
                {
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "doi": item.get("doi"),
                    "page_url": item.get("page_url", ""),
                }
                for item in state.get("candidates", [])
            ],
        }
    )
    if isinstance(decision, dict) and decision.get("cancelled"):
        return {"selected_paper_ids": [], "workflow_status": "cancelled"}
    raw_ids = decision.get("paper_ids", []) if isinstance(decision, dict) else []
    selected_ids = list(dict.fromkeys(int(paper_id) for paper_id in raw_ids))
    allowed_ids = set(state.get("candidate_paper_ids", []))
    if not selected_ids or any(paper_id not in allowed_ids for paper_id in selected_ids):
        raise ResearchPlanError("恢复值包含无效的候选论文ID")
    return {
        "selected_paper_ids": selected_ids,
        "workflow_status": "papers_selected",
    }


def route_after_paper_selection(
    state: ResearchSearchState,
) -> Literal["request_report_confirmation", "end"]:
    if state.get("workflow_status") == "papers_selected":
        return "request_report_confirmation"
    return "end"


def _request_report_decision(
    state: ResearchSearchState,
    *,
    kind: str,
) -> dict[str, Any]:
    decision = interrupt(
        {
            "kind": kind,
            "question": state["question"],
            "selected_paper_ids": state.get("selected_paper_ids", []),
            "selected_count": len(state.get("selected_paper_ids", [])),
        }
    )
    approved = bool(
        decision.get("approved", False)
        if isinstance(decision, dict)
        else decision
    )
    if not approved:
        return {
            "report_confirmed": False,
            "workflow_status": (
                "report_completed"
                if state.get("report_content")
                else "papers_selected"
            ),
        }
    return {
        "report_confirmed": True,
        "report_prepare_index": 0,
        "report_retrieve_index": 0,
        "report_evidence": [],
        "report_context_evidence": [],
        "report_context_stats": {},
        "report_context_quality": {},
        "report_access_limitations": [],
        "report_tool_calls": [],
        "report_content": "",
        "report_model": "",
        "report_citation_validation": {},
        "workflow_status": "report_preparing",
    }


def request_report_confirmation(state: ResearchSearchState) -> dict[str, Any]:
    return _request_report_decision(state, kind="report_confirmation")


def request_report_regeneration(state: ResearchSearchState) -> dict[str, Any]:
    return _request_report_decision(state, kind="report_regeneration")


def route_after_report_decision(
    state: ResearchSearchState,
) -> Literal["prepare_report_paper", "cancel_report"]:
    return (
        "prepare_report_paper"
        if state.get("report_confirmed", False)
        else "cancel_report"
    )


def cancel_report(state: ResearchSearchState) -> dict[str, Any]:
    return {
        "workflow_status": (
            "report_completed" if state.get("report_content") else "papers_selected"
        )
    }


def prepare_report_paper_node(state: ResearchSearchState) -> dict[str, Any]:
    paper_ids = state.get("selected_paper_ids", [])
    index = int(state.get("report_prepare_index", 0))
    if index >= len(paper_ids):
        return {"workflow_status": "report_retrieving"}

    result = prepare_report_paper(int(paper_ids[index]))
    tool_calls = list(state.get("report_tool_calls", []))
    access_limitations = list(state.get("report_access_limitations", []))
    if result.get("tool_call"):
        tool_calls.append(result["tool_call"])
    if result.get("access_limitation"):
        access_limitations.append(result["access_limitation"])
    return {
        "report_prepare_index": index + 1,
        "report_tool_calls": tool_calls,
        "report_access_limitations": access_limitations,
        "workflow_status": "report_preparing",
    }


def route_after_report_preparation(
    state: ResearchSearchState,
) -> Literal["prepare_report_paper", "retrieve_report_evidence"]:
    if int(state.get("report_prepare_index", 0)) < len(
        state.get("selected_paper_ids", [])
    ):
        return "prepare_report_paper"
    return "retrieve_report_evidence"


def retrieve_report_evidence_node(state: ResearchSearchState) -> dict[str, Any]:
    paper_ids = state.get("selected_paper_ids", [])
    index = int(state.get("report_retrieve_index", 0))
    if index >= len(paper_ids):
        return {"workflow_status": "report_retrieving"}

    paper_id = int(paper_ids[index])
    evidence = list(state.get("report_evidence", []))
    tool_calls = list(state.get("report_tool_calls", []))
    access_limitations = list(state.get("report_access_limitations", []))
    limited_ids = {
        int(item["paper_id"])
        for item in access_limitations
        if item.get("paper_id") is not None
    }
    if paper_id not in limited_ids:
        result = retrieve_report_paper_evidence(paper_id, state["question"])
        tool_calls.append(result["tool_call"])
        evidence.extend(result["evidence"])
        if result.get("access_limitation"):
            access_limitations.append(result["access_limitation"])
    return {
        "report_retrieve_index": index + 1,
        "report_evidence": evidence,
        "report_tool_calls": tool_calls,
        "report_access_limitations": access_limitations,
        "workflow_status": "report_retrieving",
    }


def route_after_report_retrieval(
    state: ResearchSearchState,
) -> Literal["retrieve_report_evidence", "begin_report_generation"]:
    if int(state.get("report_retrieve_index", 0)) < len(
        state.get("selected_paper_ids", [])
    ):
        return "retrieve_report_evidence"
    return "begin_report_generation"


def begin_report_generation(state: ResearchSearchState) -> dict[str, Any]:
    context_pack = pack_research_context(state.get("report_evidence", []))
    return {
        "report_context_evidence": context_pack["evidence"],
        "report_context_stats": context_pack["stats"],
        "report_context_quality": context_pack["quality_gate"],
        "workflow_status": "report_generating",
    }


def synthesize_research_report(state: ResearchSearchState) -> dict[str, Any]:
    content, model = generate_report_content(
        state["question"],
        state.get("report_context_evidence", []),
        state.get("report_access_limitations", []),
        state.get("report_context_quality", {}),
    )
    return {"report_content": content, "report_model": model}


def validate_report_citations_node(state: ResearchSearchState) -> dict[str, Any]:
    validation = validate_research_report_citations(
        state.get("report_content", ""),
        state.get("report_context_evidence", []),
    )
    return {
        "report_citation_validation": validation,
        "workflow_status": "report_completed",
    }


def build_research_search_graph():
    builder = StateGraph(ResearchSearchState)
    builder.add_node("plan_research", plan_research)
    builder.add_node("await_confirmation", await_confirmation)
    builder.add_node("multi_query_search", multi_query_search)
    builder.add_node("single_query_search", single_query_search)
    builder.add_node("deduplicate_results", deduplicate_results)
    builder.add_edge(START, "plan_research")
    builder.add_conditional_edges(
        "plan_research",
        route_after_plan,
        {
            "await_confirmation": "await_confirmation",
            "multi_query_search": "multi_query_search",
            "single_query_search": "single_query_search",
        },
    )
    builder.add_edge("await_confirmation", END)
    builder.add_edge("multi_query_search", "deduplicate_results")
    builder.add_edge("single_query_search", "deduplicate_results")
    builder.add_edge("deduplicate_results", END)
    return builder.compile()


RESEARCH_SEARCH_GRAPH = build_research_search_graph()


def build_checkpointed_research_graph(checkpointer):
    builder = StateGraph(ResearchSearchState)
    builder.add_node("plan_research", plan_research)
    builder.add_node("request_plan_confirmation", request_plan_confirmation)
    builder.add_node("multi_query_search", multi_query_search)
    builder.add_node("single_query_search", single_query_search)
    builder.add_node("cancel_research", cancel_research)
    builder.add_node("deduplicate_results", deduplicate_results)
    builder.add_node("request_paper_selection", request_paper_selection)
    builder.add_node("request_report_confirmation", request_report_confirmation)
    builder.add_node("prepare_report_paper", prepare_report_paper_node)
    builder.add_node("retrieve_report_evidence", retrieve_report_evidence_node)
    builder.add_node("begin_report_generation", begin_report_generation)
    builder.add_node("synthesize_research_report", synthesize_research_report)
    builder.add_node("validate_report_citations", validate_report_citations_node)
    builder.add_node("request_report_regeneration", request_report_regeneration)
    builder.add_node("cancel_report", cancel_report)
    builder.add_edge(START, "plan_research")
    builder.add_edge("plan_research", "request_plan_confirmation")
    builder.add_conditional_edges(
        "request_plan_confirmation",
        route_after_native_confirmation,
        {
            "multi_query_search": "multi_query_search",
            "single_query_search": "single_query_search",
            "cancel_research": "cancel_research",
        },
    )
    builder.add_edge("cancel_research", END)
    builder.add_edge("multi_query_search", "deduplicate_results")
    builder.add_edge("single_query_search", "deduplicate_results")
    builder.add_edge("deduplicate_results", "request_paper_selection")
    builder.add_conditional_edges(
        "request_paper_selection",
        route_after_paper_selection,
        {
            "request_report_confirmation": "request_report_confirmation",
            "end": END,
        },
    )
    for node_name in ("request_report_confirmation", "request_report_regeneration"):
        builder.add_conditional_edges(
            node_name,
            route_after_report_decision,
            {
                "prepare_report_paper": "prepare_report_paper",
                "cancel_report": "cancel_report",
            },
        )
    builder.add_conditional_edges(
        "prepare_report_paper",
        route_after_report_preparation,
        {
            "prepare_report_paper": "prepare_report_paper",
            "retrieve_report_evidence": "retrieve_report_evidence",
        },
    )
    builder.add_conditional_edges(
        "retrieve_report_evidence",
        route_after_report_retrieval,
        {
            "retrieve_report_evidence": "retrieve_report_evidence",
            "begin_report_generation": "begin_report_generation",
        },
    )
    builder.add_edge("begin_report_generation", "synthesize_research_report")
    builder.add_edge("synthesize_research_report", "validate_report_citations")
    builder.add_edge("validate_report_citations", "request_report_regeneration")
    builder.add_edge("cancel_report", END)
    return builder.compile(checkpointer=checkpointer)


def research_result_from_state(state: ResearchSearchState) -> ResearchSearchResult:
    return ResearchSearchResult(
        goal=state["plan_goal"],
        queries=list(state["plan_queries"]),
        rationale=state.get("plan_rationale", ""),
        model=state.get("plan_model", ""),
        status=state["plan_status"],
        error=state.get("plan_error", ""),
        workflow_status=state.get("workflow_status", ""),
        candidates=[PaperCandidate(**item) for item in state.get("candidates", [])],
        tool_calls=list(state.get("tool_calls", [])),
    )


def plan_research_search(question: str, max_results: int) -> ResearchSearchResult:
    state = RESEARCH_SEARCH_GRAPH.invoke(
        {
            "question": question,
            "max_results": max_results,
            "confirmed": False,
        }
    )
    return research_result_from_state(state)


def run_confirmed_research_search(
    question: str,
    max_results: int,
    *,
    goal: str,
    queries: list[str],
    rationale: str,
    model: str,
    status: str,
    error: str,
) -> ResearchSearchResult:
    state = RESEARCH_SEARCH_GRAPH.invoke(
        {
            "question": question,
            "max_results": max_results,
            "confirmed": True,
            "plan_goal": goal,
            "plan_queries": queries,
            "plan_rationale": rationale,
            "plan_model": model,
            "plan_status": status,
            "plan_error": error,
        }
    )
    return research_result_from_state(state)


def run_research_search(question: str, max_results: int) -> ResearchSearchResult:
    plan = plan_research_search(question, max_results)
    return run_confirmed_research_search(
        question,
        max_results,
        goal=plan.goal,
        queries=plan.queries,
        rationale=plan.rationale,
        model=plan.model,
        status=plan.status,
        error=plan.error,
    )

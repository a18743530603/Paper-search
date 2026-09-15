from dataclasses import dataclass
from typing import Any, Sequence

from .config import RESEARCH_FINAL_SEED_COUNT
from .context_service import pack_research_context
from .research_report_service import retrieve_report_paper_evidence
from .retrieval_query_service import route_retrieval_query


@dataclass(slots=True)
class ResearchResult:
    """Evidence-acquisition result consumed by Research Chat answer generation.

    The object contains observations from the existing deterministic retrieval
    pipeline only.  It is not an answer, conversation message, or source of
    paper facts beyond the original evidence excerpts it carries.
    """

    evidence: list[dict[str, Any]]
    access_limitations: list[dict[str, Any]]
    quality_gate: dict[str, Any]
    diagnostics: dict[str, Any]

    def as_context_fields(self) -> dict[str, Any]:
        """Expose the legacy ContextBuilder-compatible field layout."""

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
        }


def acquire_research_evidence(
    question: str,
    paper_ids: Sequence[int],
) -> ResearchResult:
    """Run the current production RAG once and return a stable observation.

    Query routing remains shared across all selected papers.  Each paper then
    uses the existing retrieval implementation, and the combined evidence is
    passed through the existing shared context allocation unchanged.
    """

    evidence: list[dict[str, Any]] = []
    access_limitations: list[dict[str, Any]] = []
    retrieval_tool_calls: list[dict[str, Any]] = []
    query_route = route_retrieval_query(question)

    for raw_paper_id in paper_ids:
        paper_id = int(raw_paper_id)
        if query_route.lexical_query != query_route.original_query:
            result = retrieve_report_paper_evidence(
                paper_id,
                question,
                lexical_query=query_route.lexical_query,
                top_k=RESEARCH_FINAL_SEED_COUNT,
            )
        else:
            result = retrieve_report_paper_evidence(
                paper_id,
                question,
                top_k=RESEARCH_FINAL_SEED_COUNT,
            )

        evidence.extend(result["evidence"])
        if result.get("tool_call"):
            retrieval_tool_calls.append(dict(result["tool_call"]))
        if result.get("access_limitation"):
            access_limitations.append(dict(result["access_limitation"]))

    context_pack = pack_research_context(evidence)
    retrieval_diagnostics = [
        {
            "paper_id": (tool_call.get("input") or {}).get("paper_id"),
            **dict(tool_call.get("diagnostics") or {}),
        }
        for tool_call in retrieval_tool_calls
        if tool_call.get("diagnostics")
    ]
    return ResearchResult(
        evidence=context_pack["evidence"],
        access_limitations=access_limitations,
        quality_gate=dict(context_pack["quality_gate"]),
        diagnostics={
            "query_routing": query_route.to_dict(),
            "retrieval_tool_calls": retrieval_tool_calls,
            "retrieval_diagnostics": retrieval_diagnostics,
        },
    )

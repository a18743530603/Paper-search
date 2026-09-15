from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence

from .research_evidence_service import (
    ResearchResult,
    acquire_research_evidence,
)
from .research_section_service import (
    LIST_SECTIONS_TOOL_NAME,
    PaperSection,
    READ_SECTION_TOOL_NAME,
    SectionListObservation,
    SectionListStatus,
    SectionReadObservation,
    SectionReadStatus,
    collect_paper_sections,
    read_paper_section,
)
from .research_text_search_service import (
    FIND_IN_PAPER_TOOL_NAME,
    FindInPaperObservation,
    FindInPaperStatus,
    find_paper_text,
)
from .research_tool_models import PaperChunkEvidence
from .research_visual_inspection_service import (
    INSPECT_TABLES_FIGURES_TOOL_NAME,
    VisualInspectionObservation,
    inspect_paper_visual,
)


GATHER_EVIDENCE_TOOL_NAME = "gather_evidence"
COMPLETE_RESEARCH_ACTION_NAME = "complete_research"
GATHER_EVIDENCE_TOOL_DESCRIPTION = (
  "当用户的问题需要从论文正文中寻找证据，且无法预先确定答案具体位于哪个章节、页面或图表时，"
    "使用该工具从当前已选择的原始论文中进行通用语义证据检索。"
    "适用于方法原理、研究动机、论文贡献、结果原因解释、方法比较、实验现象解释，以及答案可能分散在多个章节中的问题。"
    "如果当前已有证据不足，也可以换一个更具体的研究角度再次调用该工具补充证据。"
    "不要用于已经明确知道目标章节的问题、精确查找术语或参数的问题、明确针对某个表格或图片的问题，"
    "以及只需要查看论文章节结构的问题；这些情况应优先使用对应的专用研究工具。"
    "该工具只返回带有论文来源、章节、物理页码和正文内容的原始论文证据，不生成最终回答。"
)
LIST_SECTIONS_TOOL_DESCRIPTION = (
    "当需要查看论文真实章节结构、确认某个章节是否存在，"
    "或获取准确章节路径以便后续定点读取时使用。"
    "该工具返回指定论文当前已解析并持久化、可可靠导航的章节及子章节结构。"
    "该工具不读取章节正文、不回答论文事实、不做语义检索，"
    "也不补造、映射或猜测不存在的章节。"
)
READ_SECTION_TOOL_DESCRIPTION = (
    "当已经知道某篇论文真实、完整的章节路径，并需要定点读取该章节自身的原始正文证据时使用。"
    "章节路径应优先来自 list_sections；该工具只进行大小写敏感的精确路径读取，"
    "返回按原始顺序排列、保留物理页码和 Chunk provenance 的正文 Evidence。"
    "该工具不做语义检索、模糊匹配、章节别名映射、子章节递归读取、Parent Expansion，"
    "也不拼接、总结或改写正文。"
)
FIND_IN_PAPER_TOOL_DESCRIPTION = (
    "当已经知道需要查找的精确术语、参数、指标或连续文本表达，"
    "但不知道它位于论文哪个章节或页面时使用。"
    "该工具在指定论文的已解析原始正文中执行忽略大小写的确定性连续字面匹配，"
    "并返回保留章节、物理页码和 Chunk provenance 的原始正文 Evidence。"
    "对于原因、贡献、方法原理、结果解释等需要理解语义的宽泛问题，应使用 gather_evidence。"
    "该工具不做语义搜索、词干化、正则、OR/AND、近义词扩展、问题改写、"
    "Parent Expansion 或答案生成。"
)
INSPECT_TABLES_FIGURES_TOOL_DESCRIPTION = (
    "当已经确定需要查看论文中的某个真实表格、图片或图表，并已获得其对应的视觉 Chunk 时使用。"
    "该工具根据原始论文 PDF、物理页码和 bbox 提取该视觉元素的真实图像，"
    "同时返回相关章节、页码、原始文本和 Chunk provenance。"
    "适用于结果表、消融表、参数分析图、模型结构图、流程图、曲线图等需要实际查看视觉内容的问题。"
    "该工具不做通用语义检索、不猜测或补造不存在的图表，也不负责解释图片或生成最终答案。"
)
COMPLETE_RESEARCH_ACTION_DESCRIPTION = (
    "当当前累计的原始论文证据和Coverage已经足以进入最终回答，"
    "或继续研究的预期价值很低时选择。该函数只结束研究阶段，"
    "不执行Research Tool、不生成最终答案，也不修改现有Evidence或Coverage。"
)


def _function_tool_schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: Sequence[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


RESEARCH_AGENT_NATIVE_TOOL_SCHEMAS = (
    _function_tool_schema(
        GATHER_EVIDENCE_TOOL_NAME,
        GATHER_EVIDENCE_TOOL_DESCRIPTION,
        {
            "question": {"type": "string", "minLength": 1},
            "paper_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "uniqueItems": True,
            },
        },
        ("question", "paper_ids"),
    ),
    _function_tool_schema(
        LIST_SECTIONS_TOOL_NAME,
        LIST_SECTIONS_TOOL_DESCRIPTION,
        {"paper_id": {"type": "integer", "minimum": 1}},
        ("paper_id",),
    ),
    _function_tool_schema(
        READ_SECTION_TOOL_NAME,
        READ_SECTION_TOOL_DESCRIPTION,
        {
            "paper_id": {"type": "integer", "minimum": 1},
            "section_path": {"type": "string", "minLength": 1},
        },
        ("paper_id", "section_path"),
    ),
    _function_tool_schema(
        FIND_IN_PAPER_TOOL_NAME,
        FIND_IN_PAPER_TOOL_DESCRIPTION,
        {
            "paper_id": {"type": "integer", "minimum": 1},
            "query": {"type": "string", "minLength": 1},
        },
        ("paper_id", "query"),
    ),
    _function_tool_schema(
        INSPECT_TABLES_FIGURES_TOOL_NAME,
        INSPECT_TABLES_FIGURES_TOOL_DESCRIPTION,
        {
            "paper_id": {"type": "integer", "minimum": 1},
            "chunk_id": {"type": "integer", "minimum": 1},
        },
        ("paper_id", "chunk_id"),
    ),
    _function_tool_schema(
        COMPLETE_RESEARCH_ACTION_NAME,
        COMPLETE_RESEARCH_ACTION_DESCRIPTION,
        {},
        (),
    ),
)


def research_agent_native_tool_schemas() -> list[dict[str, Any]]:
    """Return an isolated copy of the native function schemas."""

    return deepcopy(RESEARCH_AGENT_NATIVE_TOOL_SCHEMAS)


@dataclass(frozen=True, slots=True)
class ResearchObservation:
    """Uniform observation returned by a future Research Chat tool call."""

    tool_name: str
    evidence: list[dict[str, Any]]
    access_limitations: list[dict[str, Any]]
    quality_gate: dict[str, Any]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-ready contract without rewriting evidence."""

        return {
            "tool_name": self.tool_name,
            "evidence": self.evidence,
            "access_limitations": self.access_limitations,
            "quality_gate": self.quality_gate,
            "diagnostics": self.diagnostics,
        }


def gather_evidence(
    question: str,
    paper_ids: Sequence[int],
) -> ResearchObservation:
    """Adapt the existing Evidence Acquisition result to a tool observation."""

    result: ResearchResult = acquire_research_evidence(question, paper_ids)
    return ResearchObservation(
        tool_name=GATHER_EVIDENCE_TOOL_NAME,
        evidence=result.evidence,
        access_limitations=result.access_limitations,
        quality_gate=result.quality_gate,
        diagnostics=result.diagnostics,
    )


def list_sections(paper_id: int) -> SectionListObservation:
    """Return persisted section navigation without reading paper content."""

    return collect_paper_sections(paper_id)


def read_section(
    paper_id: int,
    section_path: str,
) -> SectionReadObservation:
    """Return raw child evidence for one exact navigable section path."""

    return read_paper_section(paper_id, section_path)


def find_in_paper(
    paper_id: int,
    query: str,
) -> FindInPaperObservation:
    """Locate exact lexical text in raw persisted child chunks."""

    return find_paper_text(paper_id, query)


def inspect_tables_figures(
    paper_id: int,
    chunk_id: int,
) -> VisualInspectionObservation:
    """Extract one known visual child chunk from the matching PDF snapshot."""

    return inspect_paper_visual(paper_id, chunk_id)

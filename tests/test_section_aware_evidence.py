import json

from my_agent_project import (
    followup_context,
    research_conversation_service,
    research_report_service,
    section_evidence_service,
)


PAPER_PATH = "Paper A > Discussion"


def test_real_conclusion_section_has_priority_over_discussion():
    resolution = section_evidence_service.resolve_section_alias(
        "这篇论文的 Conclusion 写了什么？",
        [PAPER_PATH, "Paper A > 6. Conclusion"],
    )

    assert resolution is not None
    assert resolution["match_type"] == "direct"
    assert resolution["matched_section_title"] == "6. Conclusion"
    assert resolution["matched_section_path"] == "Paper A > 6. Conclusion"


def test_conclusion_intent_falls_back_to_real_discussion():
    resolution = section_evidence_service.resolve_section_alias(
        "论文的conclusion部分写了什么？",
        ["Paper A > Introduction", PAPER_PATH],
    )

    assert resolution is not None
    assert resolution["match_type"] == "discussion_fallback"
    assert resolution["matched_section_title"] == "Discussion"
    assert "没有单独的 Conclusion" in resolution["response_note"]


def test_missing_section_keeps_safe_not_found_without_fabrication():
    resolution = section_evidence_service.resolve_section_alias(
        "Conclusion 写了什么？",
        ["Paper A > Introduction", "Paper A > Experiments"],
    )
    evidence = section_evidence_service.apply_section_alignment(
        [{"chunk_id": 1, "section_path": "Paper A > Experiments"}],
        resolution,
    )

    assert resolution is not None
    assert resolution["match_type"] == "not_found"
    assert resolution["matched_section_path"] == ""
    assert "section_alignment" not in evidence[0]


def test_discussion_metadata_and_alignment_reach_followup_prompt():
    resolution = section_evidence_service.resolve_section_alias(
        "Conclusion 写了什么？",
        [PAPER_PATH],
    )
    evidence = section_evidence_service.apply_section_alignment(
        [
            {
                "paper_title": "Paper A",
                "page_number": 15,
                "chunk_id": 8,
                "content": "Concluding discussion content.",
                "section_title": "Discussion",
                "section_path": PAPER_PATH,
            }
        ],
        resolution,
    )

    payload = followup_context.build_followup_context(
        "Conclusion 写了什么？",
        "",
        evidence,
    )
    prompt = research_conversation_service.build_research_followup_prompt(
        "Conclusion 写了什么？",
        "",
        evidence,
        (),
    )

    assert payload["evidence"][0]["section_title"] == "Discussion"
    assert payload["evidence"][0]["section_path"] == PAPER_PATH
    assert payload["evidence"][0]["physical_page"] == 15
    assert payload["evidence"][0]["section_alignment"]["match_type"] == (
        "discussion_fallback"
    )
    serialized = json.loads(prompt[prompt.index("{") :])
    assert serialized["evidence"][0]["section_title"] == "Discussion"
    assert "没有单独的 Conclusion" in serialized["evidence"][0][
        "section_alignment"
    ]["response_note"]


def test_research_evidence_wrapper_preserves_real_section_metadata(monkeypatch):
    resolution = section_evidence_service.resolve_section_alias(
        "Conclusion 写了什么？",
        [PAPER_PATH],
    )
    monkeypatch.setattr(
        research_report_service,
        "invoke_research_tool",
        lambda _name, _input: {
            "tool_name": "retrieve_indexed_evidence",
            "input": _input,
            "status": "succeeded",
            "result_count": 1,
            "paper_title": "Paper A",
            "page_url": "",
            "error": "",
            "duration_ms": 1,
            "evidence": [
                {
                    "chunk_id": 8,
                    "page_number": 15,
                    "content": "Discussion content.",
                    "score": 0.9,
                    "section_title": PAPER_PATH,
                    "section_path": PAPER_PATH,
                    "section_alignment": resolution,
                }
            ],
        },
    )

    result = research_report_service.retrieve_report_paper_evidence(
        7,
        "Conclusion 写了什么？",
    )

    item = result["evidence"][0]
    assert item["section_title"] == "Discussion"
    assert item["section_path"] == PAPER_PATH
    assert item["section_alignment"]["match_type"] == "discussion_fallback"
    assert item["page_number"] == 15


def test_legacy_report_payload_preserves_section_without_changing_page():
    payload = research_report_service.build_research_context_payload(
        "Conclusion?",
        [
            {
                "paper_id": 7,
                "paper_title": "Paper A",
                "page_number": 15,
                "chunk_id": 8,
                "content": "Discussion content.",
                "section_title": "Discussion",
                "section_path": PAPER_PATH,
            }
        ],
        (),
    )

    assert payload["evidence"][0]["section_title"] == "Discussion"
    assert payload["evidence"][0]["physical_page"] == 15


def test_section_metadata_does_not_change_citation_page_validation():
    evidence = [
        {
            "paper_title": "Paper A",
            "page_number": 15,
            "section_title": "Discussion",
            "section_path": PAPER_PATH,
        }
    ]

    normalized = research_conversation_service.normalize_research_followup_citations(
        "结论性内容。[Paper A, p.15]",
        evidence,
    )

    assert normalized == "结论性内容。[Paper A，第15页]"

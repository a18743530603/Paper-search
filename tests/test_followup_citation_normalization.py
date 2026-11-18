import pytest

from my_agent_project import research_conversation_service


EVIDENCE = [{"paper_title": "Paper A", "page_number": 4}]


@pytest.mark.parametrize(
    ("citation", "expected"),
    [
        ("[Paper A, p.4]", "[Paper A，第4页]"),
        ("[Paper A, p. 4]", "[Paper A，第4页]"),
        ("[Paper A, page 4]", "[Paper A，第4页]"),
        ("[Paper A，第 4 页]", "[Paper A，第4页]"),
        ("[Paper A，第4页]", "[Paper A，第4页]"),
    ],
)
def test_normalize_followup_citation_formats(citation, expected):
    assert research_conversation_service.normalize_research_followup_citations(
        citation,
        EVIDENCE,
    ) == expected


def test_normalizer_handles_multiple_citations_and_both_answer_languages():
    content = (
        "中文结论[Paper A, p.4]。 "
        "The English conclusion is supported [Paper A, page 4]."
    )

    assert research_conversation_service.normalize_research_followup_citations(
        content,
        EVIDENCE,
    ) == (
        "中文结论[Paper A，第4页]。 "
        "The English conclusion is supported [Paper A，第4页]."
    )


def test_final_chinese_followup_citation_passes_shared_validator():
    validation = research_conversation_service.validate_research_report_citations(
        "结论由原文支持。[Paper A，第4页]",
        EVIDENCE,
    )

    assert validation["status"] == "passed"
    assert validation["citation_count"] == 1
    assert validation["valid_citation_count"] == 1


@pytest.mark.parametrize(
    "citation",
    [
        "[Unknown Paper, p.4]",
        "[Paper A, p.9]",
    ],
)
def test_normalizer_leaves_citations_rejected_by_existing_validator_unchanged(citation):
    assert research_conversation_service.normalize_research_followup_citations(
        citation,
        EVIDENCE,
    ) == citation


def test_normalizer_does_not_touch_ordinary_page_text():
    content = "See page 4 and p.4 in the prose, not as citations. 第4页也是普通文本。"

    assert research_conversation_service.normalize_research_followup_citations(
        content,
        EVIDENCE,
    ) == content


def test_normalizer_preserves_title_and_page_values():
    content = "[Paper A, page 4] [Paper A, p. 9]"

    assert research_conversation_service.normalize_research_followup_citations(
        content,
        EVIDENCE,
    ) == "[Paper A，第4页] [Paper A, p. 9]"

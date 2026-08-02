import csv
import sqlite3
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from my_agent_project import db
from my_agent_project import evaluation_service
from my_agent_project import generation_evaluation_service
from my_agent_project import model_service
from my_agent_project import pdf_service
from my_agent_project import rag_service

from my_agent_project.download_service import can_attempt_download, sanitize_filename
from my_agent_project.origin_service import build_origin_chart_tables
from my_agent_project.pdf_service import (
    build_pdf_chunk_layout,
    build_pdf_chunks,
    build_semantic_pdf_chunks,
    detect_section_heading,
    split_academic_page_text,
    split_academic_sentence_page_text,
    split_page_text,
    split_sentence_aligned_text,
    split_table_context_page_text,
    extract_table_contexts,
    is_table_caption,
    merge_semantic_units,
    semantic_breakpoint_threshold,
)
from my_agent_project.rag_service import (
    VECTOR_MODEL,
    build_access_notice,
    build_rag_prompt,
    chunk_index_text,
    index_paper_chunks,
    reciprocal_rank_fusion,
    retrieve_relevant_chunks,
)
from my_agent_project.schemas import PaperChunk, PaperParentChunk
from my_agent_project.schemas import STATUS_AVAILABLE, STATUS_LINK_ONLY
from my_agent_project.search_service import (
    parse_arxiv_feed,
    parse_crossref_items,
    search_all,
)


ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2401.00001v1</id>
    <title> Multimodal Agent: A Survey? </title>
    <summary> A useful summary. </summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Lee</name></author>
  </entry>
</feed>
"""


def test_parse_arxiv_feed_builds_pdf_url():
    papers = parse_arxiv_feed(ARXIV_XML)

    assert len(papers) == 1
    paper = papers[0]
    assert paper.title == "Multimodal Agent: A Survey?"
    assert paper.authors == "Alice Smith, Bob Lee"
    assert paper.published == "2024-01-01"
    assert paper.publisher == "arXiv"
    assert paper.page_url == "https://arxiv.org/abs/2401.00001v1"
    assert paper.pdf_url == "https://arxiv.org/pdf/2401.00001v1.pdf"
    assert paper.status == STATUS_AVAILABLE


def test_parse_crossref_without_direct_pdf_is_link_only():
    papers = parse_crossref_items(
        [
            {
                "title": ["Crossref Paper"],
                "DOI": "10.1000/example",
                "publisher": "Elsevier BV",
                "URL": "https://doi.org/10.1000/example",
                "author": [{"given": "Carol", "family": "Wang"}],
                "created": {"date-parts": [[2025, 5, 1]]},
                "link": [{"URL": "https://publisher.example/article", "content-type": "text/html"}],
            }
        ]
    )

    assert len(papers) == 1
    assert papers[0].doi == "10.1000/example"
    assert papers[0].publisher == "Elsevier BV"
    assert papers[0].pdf_url is None
    assert papers[0].status == STATUS_LINK_ONLY


def test_parse_crossref_with_absolute_pdf_can_download():
    papers = parse_crossref_items(
        [
            {
                "title": ["Open PDF"],
                "URL": "https://doi.org/10.1000/pdf",
                "link": [{"URL": "https://example.org/file.pdf", "content-type": "application/pdf"}],
            }
        ]
    )

    assert papers[0].pdf_url == "https://example.org/file.pdf"
    assert papers[0].status == STATUS_AVAILABLE


def test_sanitize_filename_removes_invalid_chars_and_limits_length():
    title = 'A:/Very*Long?Title"With<Bad>|Chars' + "x" * 200
    filename = sanitize_filename(title)

    assert filename.endswith(".pdf")
    assert len(Path(filename).stem) <= 100
    for char in '\\/*?:"<>|':
        assert char not in filename


def test_download_policy_prefers_arxiv_and_direct_pdf_only():
    assert can_attempt_download("arxiv", "https://arxiv.org/pdf/2401.00001.pdf")
    assert can_attempt_download("crossref", "https://example.org/article.pdf")
    assert not can_attempt_download("crossref", "https://example.org/article")
    assert not can_attempt_download("crossref", None)


def test_search_all_turns_source_failure_into_failed_record(monkeypatch):
    def fail_search(_query, _max_results):
        raise RuntimeError("network down")

    monkeypatch.setattr("my_agent_project.search_service.search_arxiv", fail_search)
    monkeypatch.setattr("my_agent_project.search_service.search_crossref", fail_search)

    papers = search_all("agent", 4)

    assert len(papers) == 2
    assert {paper.status for paper in papers} == {"failed"}


def test_build_origin_chart_tables_counts_key_fields():
    tables = build_origin_chart_tables(
        [
            {
                "source": "arxiv",
                "status": "downloaded",
                "publisher": "arXiv",
                "published": "2024-01-01",
            },
            {
                "source": "crossref",
                "status": "link_only",
                "publisher": "Elsevier BV",
                "published": "2025-05-01",
            },
            {
                "source": "crossref",
                "status": "link_only",
                "publisher": "Elsevier BV",
                "published": "",
            },
        ]
    )

    assert tables["source"] == [("crossref", 2), ("arxiv", 1)]
    assert tables["status"] == [("link_only", 2), ("downloaded", 1)]
    assert tables["publisher"] == [("Elsevier BV", 2), ("arXiv", 1)]
    assert tables["year"] == [("2024", 1), ("2025", 1), ("Unknown", 1)]


def test_split_page_text_keeps_overlap_and_limits_chunks():
    text = "First paragraph. " * 80
    chunks = split_page_text(text, chunk_size=200, overlap=30)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 200 for chunk in chunks)


def test_detect_section_heading_supports_academic_numbering():
    assert detect_section_heading("4 Experiments") == (1, "4 Experiments")
    assert detect_section_heading("4.1 Experimental Settings") == (
        2,
        "4.1 Experimental Settings",
    )
    assert detect_section_heading("IV. Results and Discussion") == (
        1,
        "IV Results and Discussion",
    )
    assert detect_section_heading("Figure 2. Main results") is None
    assert detect_section_heading("I use AdamW for all experiments") is None
    assert detect_section_heading("C. Feature Importance Analysis via SHAP") == (
        2,
        "C Feature Importance Analysis via SHAP",
    )
    assert detect_section_heading("2 values violate the assumption") is None
    assert detect_section_heading("8 NumHDonors 0.063 -0.275") is None


def test_academic_chunks_keep_section_path_across_pages():
    first_page, section_path = split_academic_page_text(
        "4 Experiments\nOverview of the experiments.\n"
        "4.1 Experimental Settings\nWe use AdamW with learning rate 1e-3.",
        chunk_size=400,
        overlap=40,
    )
    second_page, section_path = split_academic_page_text(
        "Training runs for five epochs.",
        current_sections=section_path,
        chunk_size=400,
        overlap=40,
    )

    assert first_page[0].startswith("[Section: 4 Experiments]")
    assert first_page[1].startswith(
        "[Section: 4 Experiments > 4.1 Experimental Settings]"
    )
    assert second_page[0].startswith(
        "[Section: 4 Experiments > 4.1 Experimental Settings]"
    )
    assert section_path == ["4 Experiments", "4.1 Experimental Settings"]


def test_sentence_aligned_chunks_keep_complete_sentence_overlap():
    sentence_one = "The first experiment uses a carefully balanced dataset."
    sentence_two = "The second experiment applies AdamW for five epochs."
    sentence_three = "The final experiment reports accuracy and RMSE."

    chunks = split_sentence_aligned_text(
        " ".join((sentence_one, sentence_two, sentence_three)),
        chunk_size=120,
        overlap=40,
    )

    assert chunks == [
        f"{sentence_one} {sentence_two}",
        f"{sentence_two} {sentence_three}",
    ]
    assert all(chunk.endswith(".") for chunk in chunks)


def test_sentence_aligned_chunks_do_not_split_academic_abbreviations():
    chunks = split_sentence_aligned_text(
        "The method follows Smith et al. and uses Fig. 2 for comparison. "
        "The final score is reported separately.",
        chunk_size=70,
        overlap=10,
    )

    assert chunks[0] == "The method follows Smith et al. and uses Fig. 2 for comparison."
    assert chunks[1] == "The final score is reported separately."


def test_sentence_aligned_chunks_bound_unpunctuated_table_text():
    chunks = split_sentence_aligned_text(
        " ".join(f"metric_{index} value_{index}" for index in range(120)),
        chunk_size=180,
        overlap=20,
    )

    assert len(chunks) > 1
    assert all(len(chunk) <= 180 for chunk in chunks)


def test_academic_sentence_chunks_inherit_section_path_across_pages():
    first_page, section_path = split_academic_sentence_page_text(
        "4 Experiments\nThe experiments compare three models.\n"
        "4.1 Settings\nAdamW is used for five epochs.",
        chunk_size=400,
        overlap=40,
    )
    second_page, section_path = split_academic_sentence_page_text(
        "The learning rate is 1e-3.",
        current_sections=section_path,
        chunk_size=400,
        overlap=40,
    )

    assert first_page[0].startswith("[Section: 4 Experiments]")
    assert first_page[1].startswith("[Section: 4 Experiments > 4.1 Settings]")
    assert second_page[0].startswith("[Section: 4 Experiments > 4.1 Settings]")
    assert section_path == ["4 Experiments", "4.1 Settings"]


def test_build_pdf_chunks_supports_paper_section_sentence_strategy():
    chunks = build_pdf_chunks(
        [
            "2 Methods\nThe method first extracts features. "
            "It then trains a classifier."
        ],
        strategy="paper_section_sentence",
        chunk_size=100,
        overlap=20,
    )

    assert chunks
    assert chunks[0].page_number == 1
    assert chunks[0].content.startswith("[Section: 2 Methods]")


def test_parent_child_layout_uses_small_sentence_children_and_fixed_parents():
    text = " ".join(
        [
            "The model extracts multimodal features from every input.",
            "AdamW trains the classifier with a stable learning rate.",
            "The validation split is used for early stopping.",
            "The final evaluation reports accuracy and calibration error.",
            "Ablation studies compare every component of the method.",
            "The discussion explains the remaining limitations.",
        ]
    )

    children, parents = build_pdf_chunk_layout(
        [text],
        strategy="paper_parent_child",
        chunk_size=600,
        overlap=60,
    )

    assert len(parents) == 1
    assert len(children) >= 2
    assert all(child.parent_index == parents[0].parent_index for child in children)
    assert all(len(child.content) <= 300 for child in children)
    assert parents[0].content == text


def test_parent_child_retrieval_returns_and_deduplicates_parent(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(1, 0, "AdamW optimizer trains the model.", 0),
            PaperChunk(1, 1, "The learning rate is 1e-3.", 0),
            PaperChunk(2, 2, "AdamW was introduced in earlier work.", 1),
        ],
        parent_chunks=[
            PaperParentChunk(
                1,
                0,
                "AdamW optimizer trains the model. The learning rate is 1e-3.",
            ),
            PaperParentChunk(2, 1, "AdamW was introduced in earlier work."),
        ],
        page_count=2,
        chunk_strategy="paper_parent_child",
        chunk_size=1200,
        chunk_overlap=150,
    )

    index_paper_chunks(paper_id)
    results = retrieve_relevant_chunks(
        paper_id,
        "What learning rate does AdamW use?",
        semantic_weight=0,
        keyword_weight=1,
        top_k=5,
    )

    assert results[0]["parent_chunk_id"] is not None
    assert results[0]["content"].endswith("The learning rate is 1e-3.")
    assert results[0]["retrieval_content"] != results[0]["content"]
    assert len({result["parent_chunk_id"] for result in results}) == len(results)


def test_table_caption_detection_rejects_reference_sentences():
    assert is_table_caption("Table 4. W/D/L records for all classifiers")
    assert is_table_caption("TABLE IV")
    assert is_table_caption("Table 7 continued")
    assert not is_table_caption("Table 4 gives the W/D/L records of the BNCs.")
    assert not is_table_caption("Table III presents the quantitative ranking.")


def test_table_context_keeps_caption_header_and_original_body_layout():
    before = "The experiment setup is described before the results. " * 8
    table = (
        "Table 2\n"
        "Predictive performance for all models.\n"
        "Model RMSE CRPS Coverage\n"
        "Baseline 36.3 8.49 0.957\n"
        "Proposed 19.1 6.07 0.946\n"
        "Values in bold indicate the best result.\n"
    )
    after = "The proposed model achieves the best balance across metrics. " * 8
    text = before + "\n" + table + after

    baseline = split_page_text(text, chunk_size=260, overlap=30)
    contextual = split_table_context_page_text(
        text,
        chunk_size=260,
        overlap=30,
    )
    contexts = extract_table_contexts(text)

    assert len(contextual) == len(baseline)
    assert contexts[0][0] == "Table 2"
    assert "Model RMSE CRPS Coverage" in contexts[0][1]
    assert not contextual[0].startswith("[Table Context]")
    assert any(chunk.startswith("[Table Context]\nTable 2") for chunk in contextual)
    assert all(
        original in enhanced
        for original, enhanced in zip(baseline, contextual)
    )


def test_semantic_breakpoint_splits_at_low_adjacent_similarity():
    units = [
        "Classifiers estimate posterior probabilities. " + "a" * 125,
        "Bayesian networks model dependent attributes. " + "b" * 125,
        "Quantum states are represented by wave functions. " + "c" * 125,
        "Particle measurements collapse the state. " + "d" * 125,
    ]
    vectors = [
        [1.0, 0.0],
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 1.0],
    ]

    chunks, break_count = merge_semantic_units(
        units,
        vectors,
        chunk_size=600,
        overlap=0,
        percentile=25,
    )

    assert semantic_breakpoint_threshold([1.0, 0.0, 1.0], percentile=25) == 0.5
    assert break_count == 1
    assert len(chunks) == 2
    assert "Bayesian networks" in chunks[0]
    assert "Quantum states" not in chunks[0]
    assert chunks[1].startswith("Quantum states")
    assert all(len(chunk) <= 600 for chunk in chunks)


def test_semantic_chunk_layout_uses_seed_units_and_section_prefix():
    sentences = [
        "Bayesian classifiers estimate posterior probabilities " + "a" * 105 + ".",
        "Dependent attributes are represented in a network " + "b" * 110 + ".",
        "Quantum states are represented by wave functions " + "c" * 110 + ".",
        "Particle measurements collapse the quantum state " + "d" * 110 + ".",
    ]
    captured: list[str] = []

    def fake_embedder(texts: list[str]) -> list[list[float]]:
        captured.extend(texts)
        return [
            [0.0, 1.0] if "Quantum" in text or "quantum" in text else [1.0, 0.0]
            for text in texts
        ]

    stats: dict[str, int] = {}
    chunks = build_semantic_pdf_chunks(
        ["2 Methods\n" + " ".join(sentences)],
        semantic_embedder=fake_embedder,
        chunk_size=600,
        overlap=0,
        layout_stats=stats,
    )

    assert len(captured) == 4
    assert all(text.startswith("[Section: 2 Methods]") for text in captured)
    assert all(chunk.content.startswith("[Section: 2 Methods]") for chunk in chunks)
    assert stats["semantic_unit_count"] == 4
    assert stats["semantic_break_count"] == 1
    assert stats["semantic_threshold_scope"] == "paper"
    assert stats["semantic_threshold"] == 0.5


def test_paper_level_semantic_threshold_avoids_forced_local_breaks():
    topic_a = [
        "Bayesian classifiers estimate probabilities " + char * 175 + "."
        for char in ("a", "b")
    ]
    topic_b = "Quantum states use wave functions " + "c" * 185 + "."
    stable = [
        "The same experiment reports another stable result " + char * 165 + "."
        for char in ("d", "e", "f")
    ]
    page = (
        "2 Methods\n"
        + " ".join([*topic_a, topic_b])
        + "\n3 Results\n"
        + " ".join(stable)
    )

    def fake_embedder(texts: list[str]) -> list[list[float]]:
        return [
            [0.0, 1.0] if "Quantum" in text else [1.0, 0.0]
            for text in texts
        ]

    global_stats: dict[str, object] = {}
    local_stats: dict[str, object] = {}
    build_semantic_pdf_chunks(
        [page],
        semantic_embedder=fake_embedder,
        layout_stats=global_stats,
        threshold_scope="paper",
    )
    build_semantic_pdf_chunks(
        [page],
        semantic_embedder=fake_embedder,
        layout_stats=local_stats,
        threshold_scope="segment",
    )

    assert global_stats["semantic_break_count"] == 1
    assert local_stats["semantic_break_count"] == 2


def test_academic_prefix_ablation_keeps_identical_chunk_layout():
    pages = [
        "4 Experiments\nOverview of the experiments.\n"
        "4.1 Experimental Settings\nWe use AdamW with learning rate 1e-3."
    ]

    with_prefix = build_pdf_chunks(
        pages,
        strategy="academic_section",
        chunk_size=400,
        overlap=40,
    )
    without_prefix = build_pdf_chunks(
        pages,
        strategy="academic_section_no_prefix",
        chunk_size=400,
        overlap=40,
    )

    assert with_prefix == without_prefix
    assert chunk_index_text(
        with_prefix[-1].content,
        "academic_section_no_prefix",
    ) == "We use AdamW with learning rate 1e-3."
    assert chunk_index_text(
        with_prefix[-1].content,
        "academic_section",
    ).startswith("[Section: 4 Experiments > 4.1 Experimental Settings]")


def test_academic_chunks_exclude_reference_section():
    chunks, section_path = split_academic_page_text(
        "References\n[1] A cited work.\n[2] Another cited work.",
        chunk_size=400,
        overlap=40,
    )

    assert chunks == []
    assert section_path == ["References"]


def test_academic_chunks_merge_wrapped_ieee_headings_and_lock_references():
    chunks, section_path = split_academic_page_text(
        "II. M\n"
        "ATERIALS AND METHODS\n"
        "A. D\n"
        "ataset Construction and Quality Control\n"
        "The dataset contains complete records.\n"
        "References\n"
        "H. Duan, J. Wang, and Y. Qiao. A cited paper.\n",
        chunk_size=400,
        overlap=40,
    )

    assert len(chunks) == 1
    assert chunks[0].startswith(
        "[Section: II MATERIALS AND METHODS > "
        "A Dataset Construction and Quality Control]"
    )
    assert "cited paper" not in chunks[0]
    assert section_path == ["References"]


def test_academic_chunks_resume_after_explicit_appendix_heading():
    chunks, section_path = split_academic_page_text(
        "References\n"
        "H. Duan, J. Wang, and Y. Qiao. A cited paper.\n"
        "A. Appendix\n"
        "A.1. Training Details\n"
        "AdamW is used for five epochs.",
        chunk_size=400,
        overlap=40,
    )

    assert len(chunks) == 1
    assert chunks[0].startswith("[Section: A Appendix > A.1 Training Details]")
    assert "cited paper" not in chunks[0]
    assert section_path == ["A Appendix", "A.1 Training Details"]


def test_access_notice_mentions_legal_access_and_link():
    paper = {
        "title": "Paywalled paper",
        "page_url": "https://doi.org/10.1000/example",
        "doi": "10.1000/example",
    }

    notice = build_access_notice(paper)

    assert "购买" in notice
    assert "机构订阅" in notice
    assert paper["page_url"] in notice


def test_rag_prompt_does_not_pretend_full_text_is_available():
    paper = {
        "title": "Metadata only paper",
        "page_url": "https://publisher.example/paper",
        "doi": "10.1000/example",
    }

    prompt = build_rag_prompt("论文结论是什么？", paper, [])

    assert "没有可用的全文片段" in prompt
    assert "可能需要在出版商网站购买" in prompt
    assert paper["page_url"] in prompt


def test_download_completion_records_timestamp(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.insert_papers(
        "agent",
        [parse_arxiv_feed(ARXIV_XML)[0]],
    )[0]

    db.update_download_status(
        paper_id,
        "downloaded",
        local_path=str(tmp_path / "paper.pdf"),
    )

    paper = db.get_paper(paper_id)
    assert paper is not None
    assert paper["downloaded_at"]
    assert "+" in paper["downloaded_at"] or paper["downloaded_at"].endswith("Z")


def test_clear_history_removes_database_records_only(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    local_pdf = tmp_path / "paper.pdf"
    local_pdf.write_bytes(b"pdf placeholder")
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])

    deleted_count = db.clear_paper_history()

    assert deleted_count == 1
    assert db.list_papers() == []
    assert local_pdf.exists()


def test_available_paper_renders_manual_download_button():
    template_dir = Path(__file__).parents[1] / "my_agent_project" / "templates"
    template = Environment(loader=FileSystemLoader(template_dir)).get_template("table.html")
    html = template.render(
        papers=[
            {
                "id": 1,
                "title": "Open paper",
                "authors": "Author",
                "source": "arxiv",
                "publisher": "arXiv",
                "published": "2026-01-01",
                "status": "available",
                "downloaded_at": None,
                "page_url": "https://arxiv.org/abs/1",
                "pdf_url": "https://arxiv.org/pdf/1.pdf",
                "error": None,
            }
        ]
    )

    assert 'action="/papers/1/download"' in html
    assert ">下载</button>" in html


def test_local_tfidf_index_retrieves_relevant_page(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(1, 0, "The model uses the ImageNet dataset for training."),
            PaperChunk(2, 1, "The conclusion discusses future multimodal agents."),
        ],
        page_count=2,
    )

    index_paper_chunks(paper_id)
    results = retrieve_relevant_chunks(paper_id, "Which dataset is used?")
    document = db.get_document(paper_id)

    assert document is not None
    assert document["index_status"] == "indexed"
    assert document["vectorizer"] == VECTOR_MODEL
    assert results[0]["page_number"] == 1
    assert "ImageNet" in results[0]["content"]


def test_bm25_retrieves_chunk_with_exact_academic_terms(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(
                1,
                0,
                "XLOGP3 experiments report an RMSE value of 0.731.",
            ),
            PaperChunk(
                2,
                1,
                "The discussion summarizes general prediction performance.",
            ),
        ],
        page_count=2,
    )

    index_paper_chunks(paper_id)
    results = retrieve_relevant_chunks(
        paper_id,
        "What RMSE is reported for XLOGP3?",
        semantic_weight=0,
        keyword_weight=1,
        keyword_retriever="bm25",
    )

    assert results[0]["page_number"] == 1
    assert results[0]["keyword_score"] == 1.0


def test_retrieval_rejects_unknown_keyword_retriever():
    try:
        retrieve_relevant_chunks(
            1,
            "question",
            keyword_retriever="unknown",
        )
    except ValueError as exc:
        assert "keyword retriever" in str(exc)
    else:
        raise AssertionError("unknown keyword retriever should fail")


def test_pdf_text_cache_reuses_pages_when_chunk_strategy_changes(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"fake-pdf-for-cache-test")
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.register_local_paper("Cached paper", pdf_path)
    extracted: list[Path] = []

    def fake_extract(path):
        extracted.append(path)
        return [
            "1 Introduction\nThe first page contains background.",
            "2 Methods\nThe second page contains the method.",
        ]

    monkeypatch.setattr(pdf_service, "extract_pdf_pages", fake_extract)
    first_stats: dict[str, int] = {}
    second_stats: dict[str, int] = {}
    third_stats: dict[str, int] = {}

    first = pdf_service.parse_paper_pdf(
        paper_id,
        strategy="length_boundary",
        chunk_size=300,
        overlap=30,
        cache_stats=first_stats,
    )
    second = pdf_service.parse_paper_pdf(
        paper_id,
        strategy="length_boundary",
        chunk_size=300,
        overlap=30,
        cache_stats=second_stats,
    )
    third = pdf_service.parse_paper_pdf(
        paper_id,
        strategy="academic_section",
        chunk_size=300,
        overlap=30,
        cache_stats=third_stats,
    )

    assert len(extracted) == 1
    assert first["chunks_reused"] is False
    assert second["chunks_reused"] is True
    assert third["chunks_reused"] is False
    assert first_stats["pdf_page_misses"] == 2
    assert second_stats["chunk_layout_hits"] == 2
    assert third_stats["pdf_page_hits"] == 2
    document = db.get_document(paper_id)
    assert document["chunk_strategy"] == "academic_section"
    assert document["source_hash"] == pdf_service.pdf_source_hash(pdf_path)


def test_deepseek_request_uses_configured_api(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "基于证据的回答"}}]}

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr(model_service, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(model_service, "DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(model_service.httpx, "post", fake_post)

    answer, model = model_service.ask_deepseek("system", "question")

    assert answer == "基于证据的回答"
    assert model == "deepseek-v4-pro"
    assert captured["url"].endswith("/chat/completions")
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer test-key"
    assert captured["kwargs"]["json"]["messages"][1]["content"] == "question"


def test_deepseek_json_request_uses_json_mode(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": '{"ranking": [2, 1]}'}}
                ]
            }

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr(model_service, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(model_service, "DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(model_service.httpx, "post", fake_post)

    result, model = model_service.ask_deepseek_json("json system", "rank")

    assert result == {"ranking": [2, 1]}
    assert model == "deepseek-v4-pro"
    body = captured["kwargs"]["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0
    assert body["thinking"] == {"type": "disabled"}


def test_seed_embedding_request_uses_volcano_ark(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "seed1.5-embedding-endpoint",
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ],
            }

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr(model_service, "SEED_API_KEY", "seed-key")
    monkeypatch.setattr(model_service, "SEED_EMBEDDING_MODEL", "ep-seed15")
    monkeypatch.setattr(model_service.httpx, "post", fake_post)

    vectors, model = model_service.embed_with_seed(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert model == "seed1.5-embedding-endpoint"
    assert captured["url"].endswith("/api/v3/embeddings")
    assert captured["kwargs"]["json"]["model"] == "ep-seed15"
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer seed-key"


def test_seed_embedding_vision_uses_multimodal_api(monkeypatch):
    responses = iter(
        [
            {
                "model": "doubao-embedding-vision-251215",
                "data": [{"object": "embedding", "embedding": [[1.0, 0.0]]}],
            },
            {
                "model": "doubao-embedding-vision-251215",
                "data": [{"object": "embedding", "embedding": [[0.0, 1.0]]}],
            },
        ]
    )
    captured = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        captured.append((url, kwargs))
        return FakeResponse(next(responses))

    monkeypatch.setattr(model_service, "SEED_API_KEY", "seed-key")
    monkeypatch.setattr(
        model_service,
        "SEED_EMBEDDING_MODEL",
        "doubao-embedding-vision-251215",
    )
    monkeypatch.setattr(model_service, "SEED_EMBEDDING_API_MODE", "auto")
    monkeypatch.setattr(model_service.httpx, "post", fake_post)

    vectors, model = model_service.embed_with_seed(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert model == "doubao-embedding-vision-251215"
    assert len(captured) == 2
    assert all(url.endswith("/api/v3/embeddings/multimodal") for url, _ in captured)
    assert captured[0][1]["json"]["input"] == [
        {"type": "text", "text": "first"}
    ]
    assert captured[1][1]["json"]["input"] == [
        {"type": "text", "text": "second"}
    ]


def test_hybrid_retrieval_uses_seed_semantics_and_tfidf(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(1, 0, "The neural network architecture uses attention layers."),
            PaperChunk(2, 1, "The dataset contains one thousand images."),
        ],
        page_count=2,
    )

    def fake_seed(texts):
        if len(texts) == 2:
            return [[1.0, 0.0], [0.0, 1.0]], "seed1.5-embedding"
        return [[1.0, 0.0]], "seed1.5-embedding"

    monkeypatch.setattr(rag_service, "embed_with_seed", fake_seed)

    index_paper_chunks(paper_id)
    results = retrieve_relevant_chunks(paper_id, "模型结构是如何设计的？")
    document = db.get_document(paper_id)

    assert document is not None
    assert document["vectorizer"] == "hybrid:seed1.5-embedding+local-tfidf-v1"
    assert results[0]["page_number"] == 1
    assert results[0]["semantic_score"] == 1.0
    assert results[0]["score"] >= 0.75


def test_seed_embedding_cache_avoids_repeated_chunk_and_query_calls(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)
    monkeypatch.setattr(
        rag_service,
        "seed_embedding_cache_namespace",
        lambda: "seed|test|model|text",
    )
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(1, 0, "Neural architecture and attention."),
            PaperChunk(2, 1, "Dataset contains one thousand images."),
        ],
        page_count=2,
    )
    calls: list[list[str]] = []

    def fake_seed(texts):
        calls.append(list(texts))
        return [[1.0, float(index)] for index, _ in enumerate(texts)], "test-model"

    monkeypatch.setattr(rag_service, "embed_with_seed", fake_seed)
    first_stats: dict[str, int] = {}
    second_stats: dict[str, int] = {}

    index_paper_chunks(paper_id, first_stats)
    index_paper_chunks(paper_id, second_stats)
    retrieve_relevant_chunks(
        paper_id,
        "How is the architecture designed?",
        cache_stats=first_stats,
    )
    retrieve_relevant_chunks(
        paper_id,
        "How is the architecture designed?",
        cache_stats=second_stats,
    )

    assert calls == [
        [
            "Neural architecture and attention.",
            "Dataset contains one thousand images.",
        ],
        ["How is the architecture designed?"],
    ]
    assert first_stats["chunk_embedding_misses"] == 2
    assert second_stats["chunk_embedding_hits"] == 2
    assert first_stats["query_embedding_misses"] == 1
    assert second_stats["query_embedding_hits"] == 1


def test_llm_response_cache_round_trips_json(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()

    assert db.get_cached_llm_response("rerank|test", "prompt-hash") is None
    db.upsert_cached_llm_response(
        "rerank|test",
        "prompt-hash",
        {"ranking": [2, 1]},
    )

    assert db.get_cached_llm_response("rerank|test", "prompt-hash") == {
        "ranking": [2, 1]
    }


def test_generation_evaluation_tables_round_trip_results(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    case_id = db.upsert_evaluation_case(
        paper_id,
        "事实型",
        "Which dataset is used?",
        "CIFAR-10",
        3,
        "The experiment uses CIFAR-10.",
    )
    retrieval_run_id = db.create_evaluation_run(
        name="retrieval",
        chunk_strategy="length_boundary",
        query_mode="english",
        keyword_retriever="tfidf",
        chunk_size=1200,
        chunk_overlap=150,
        embedding_model="test",
        semantic_weight=0.75,
        keyword_weight=0.25,
        top_k=5,
        case_count=1,
    )
    db.finish_evaluation_run(retrieval_run_id, "completed", metrics={})
    run_id = db.create_generation_evaluation_run(
        name="generation",
        retrieval_run_id=retrieval_run_id,
        generator_model="deepseek-test",
        judge_model="deepseek-test",
        prompt_version="v1",
        case_count=1,
    )
    db.save_generation_evaluation_result(
        run_id,
        case_id,
        answer="CIFAR-10 [第 3 页]",
        evidence=[{"page_number": 3, "content": "CIFAR-10"}],
        faithfulness=1.0,
        answer_relevance=1.0,
        reference_correctness=1.0,
        citation_correctness=1.0,
        judge={"claims": [{"supported": True}]},
        generation_seconds=1.2,
        judge_seconds=0.8,
    )
    db.finish_generation_evaluation_run(
        run_id,
        "completed",
        metrics={"faithfulness": 1.0},
    )

    run = db.get_generation_evaluation_run(run_id)
    results = db.list_generation_evaluation_results(run_id)

    assert run is not None
    assert run["retrieval_run_id"] == retrieval_run_id
    assert run["metrics"]["faithfulness"] == 1.0
    assert results[0]["answer"] == "CIFAR-10 [第 3 页]"
    assert results[0]["evidence"][0]["page_number"] == 3
    assert results[0]["judge"]["claims"][0]["supported"] is True


def test_extract_citation_pages_supports_chinese_and_english_formats():
    answer = (
        "The first result is reported on [第 5、9–10 页]. "
        "The setup appears on page 14 and pp. 16-17."
    )

    assert generation_evaluation_service.extract_citation_pages(answer) == {
        5,
        9,
        10,
        14,
        16,
        17,
    }


def test_validate_judge_response_computes_claim_level_scores():
    answer = (
        "AOD improves LLaVA-1.5 by 6.4% [第 6 页]. "
        "The optimizer is SGD [第 14 页]."
    )
    response = {
        "claims": [
            {
                "text": "AOD improves LLaVA-1.5 by 6.4%.",
                "supported": True,
                "cited_pages": [6],
                "valid_cited_pages": [6],
            },
            {
                "text": "The optimizer is SGD.",
                "supported": False,
                "cited_pages": [14],
                "valid_cited_pages": [],
            },
        ],
        "answer_relevance": 0.9,
        "reference_correctness": 0.75,
        "notes": "One unsupported claim.",
    }

    validated = generation_evaluation_service.validate_judge_response(
        response,
        answer=answer,
        evidence_pages={6, 14},
    )

    assert validated["faithfulness"] == 0.5
    assert validated["answer_relevance"] == 0.9
    assert validated["reference_correctness"] == 0.75
    assert validated["citation_correctness"] == 0.5
    assert validated["explicit_citation_pages"] == [6, 14]


def test_validate_judge_response_rejects_invented_citation_page():
    response = {
        "claims": [
            {
                "text": "AOD improves POPE.",
                "supported": True,
                "cited_pages": [7],
                "valid_cited_pages": [7],
            }
        ],
        "answer_relevance": 1.0,
        "reference_correctness": 1.0,
    }

    try:
        generation_evaluation_service.validate_judge_response(
            response,
            answer="AOD improves POPE [第 6 页].",
            evidence_pages={6},
        )
    except ValueError as exc:
        assert "没有显式出现" in str(exc)
    else:
        raise AssertionError("答案中不存在的引用页码必须被拒绝")


def test_aggregate_generation_metrics_excludes_failed_cases():
    results = [
        {
            "question_type": "事实型",
            "faithfulness": 1.0,
            "answer_relevance": 0.8,
            "reference_correctness": 0.6,
            "citation_correctness": 1.0,
            "error": None,
        },
        {
            "question_type": "方法型",
            "faithfulness": 0.5,
            "answer_relevance": 1.0,
            "reference_correctness": 0.8,
            "citation_correctness": 0.5,
            "error": None,
        },
        {
            "question_type": "事实型",
            "faithfulness": 0.0,
            "answer_relevance": 0.0,
            "reference_correctness": 0.0,
            "citation_correctness": 0.0,
            "error": "judge failed",
        },
    ]

    metrics = generation_evaluation_service._aggregate_generation_metrics(results)

    assert metrics["case_count"] == 2
    assert metrics["error_count"] == 1
    assert metrics["faithfulness"] == 0.75
    assert metrics["answer_relevance"] == 0.9
    assert metrics["reference_correctness"] == 0.7
    assert metrics["citation_correctness"] == 0.75
    assert metrics["by_question_type"]["事实型"]["case_count"] == 1


def test_no_prefix_strategy_indexes_body_text_only(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: True)
    monkeypatch.setattr(
        rag_service,
        "seed_embedding_cache_namespace",
        lambda: "seed|test|model|text",
    )
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [
            PaperChunk(
                1,
                0,
                "[Section: 4 Experiments > 4.1 Settings]\n"
                "We use AdamW with learning rate 1e-3.",
            )
        ],
        page_count=1,
        chunk_strategy="academic_section_no_prefix",
    )
    calls: list[list[str]] = []

    def fake_seed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts], "test-model"

    monkeypatch.setattr(rag_service, "embed_with_seed", fake_seed)

    index_paper_chunks(paper_id)
    rows = db.list_indexed_chunks(paper_id)

    assert calls == [["We use AdamW with learning rate 1e-3."]]
    assert "section" not in rows[0]["vector_json"]
    assert "experiments" not in rows[0]["vector_json"]


def test_rag_query_stores_answer_and_page_evidence(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [PaperChunk(3, 0, "The experiment uses the CIFAR-10 dataset.")],
        page_count=3,
    )
    index_paper_chunks(paper_id)
    query_id = db.create_rag_query(paper_id, "Which dataset is used?")
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek",
        lambda _system, _user: ("论文使用 CIFAR-10 数据集。", "deepseek-v4-pro"),
    )

    rag_service.answer_rag_query(query_id)
    query = db.get_rag_query(query_id)

    assert query is not None
    assert query["status"] == "answered"
    assert query["reranker"] == "none"
    assert query["rerank_error"] is None
    assert "CIFAR-10" in query["answer"]
    evidence = db.list_rag_queries(paper_id)[0]["evidence"]
    assert evidence[0]["page_number"] == 3


def test_init_db_adds_rag_reranker_audit_columns(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE rag_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                evidence_json TEXT,
                model TEXT,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    db.init_db()

    with db.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(rag_queries)").fetchall()
        }
    assert {"reranker", "rerank_error"} <= columns


def test_rag_query_can_rerank_top_twenty_to_top_five(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [PaperChunk(1, 0, "Indexed content.")],
        page_count=1,
    )
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    index_paper_chunks(paper_id)
    candidates = [
        {
            "chunk_id": index,
            "parent_chunk_id": None,
            "page_number": index,
            "chunk_index": index,
            "child_chunk_index": index,
            "retrieval_content": f"Candidate {index}",
            "content": f"Candidate {index}",
            "score": 1 / index,
            "semantic_score": 0.5,
            "keyword_score": 0.5,
        }
        for index in range(1, 21)
    ]
    calls: dict[str, int] = {}

    def fake_retrieve(_paper_id, _question, *, top_k, **_kwargs):
        calls["retrieval_top_k"] = top_k
        return candidates

    def fake_rerank(_question, items, *, top_k, **_kwargs):
        calls["rerank_candidate_count"] = len(items)
        calls["rerank_top_k"] = top_k
        reranked = []
        for rerank_rank, item in enumerate(reversed(items), start=1):
            stored = dict(item)
            stored["retrieval_rank"] = len(items) - rerank_rank + 1
            stored["rerank_rank"] = rerank_rank
            reranked.append(stored)
        return reranked[:top_k]

    monkeypatch.setattr(rag_service, "retrieve_relevant_chunks", fake_retrieve)
    monkeypatch.setattr(
        rag_service,
        "rerank_candidates_with_deepseek",
        fake_rerank,
    )
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek",
        lambda _system, _user: ("Reranked answer.", "deepseek-test"),
    )
    query_id = db.create_rag_query(
        paper_id,
        "Which candidate is relevant?",
        rag_service.RERANKER_DEEPSEEK_LISTWISE,
    )

    rag_service.answer_rag_query(query_id)

    query = db.get_rag_query(query_id)
    evidence = db.list_rag_queries(paper_id)[0]["evidence"]
    assert calls == {
        "retrieval_top_k": 20,
        "rerank_candidate_count": 20,
        "rerank_top_k": 5,
    }
    assert query["status"] == "answered"
    assert query["reranker"] == "deepseek_listwise"
    assert query["rerank_error"] is None
    assert len(evidence) == 5
    assert evidence[0]["content"] == "Candidate 20"
    assert evidence[0]["retrieval_rank"] == 20
    assert evidence[0]["rerank_rank"] == 1


def test_rag_query_falls_back_when_reranker_fails(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    db.replace_paper_chunks(
        paper_id,
        [PaperChunk(1, 0, "Indexed content.")],
        page_count=1,
    )
    monkeypatch.setattr(rag_service, "is_seed_configured", lambda: False)
    index_paper_chunks(paper_id)
    candidates = [
        {
            "chunk_id": index,
            "parent_chunk_id": None,
            "page_number": index,
            "chunk_index": index,
            "child_chunk_index": index,
            "retrieval_content": f"Candidate {index}",
            "content": f"Candidate {index}",
            "score": 1 / index,
            "semantic_score": 0.5,
            "keyword_score": 0.5,
        }
        for index in range(1, 21)
    ]
    monkeypatch.setattr(
        rag_service,
        "retrieve_relevant_chunks",
        lambda *_args, **_kwargs: candidates,
    )

    def fail_reranker(*_args, **_kwargs):
        raise RuntimeError("reranker unavailable")

    monkeypatch.setattr(
        rag_service,
        "rerank_candidates_with_deepseek",
        fail_reranker,
    )
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek",
        lambda _system, _user: ("Fallback answer.", "deepseek-test"),
    )
    query_id = db.create_rag_query(
        paper_id,
        "Can fallback still answer?",
        rag_service.RERANKER_DEEPSEEK_LISTWISE,
    )

    rag_service.answer_rag_query(query_id)

    query = db.get_rag_query(query_id)
    evidence = db.list_rag_queries(paper_id)[0]["evidence"]
    assert query["status"] == "answered"
    assert query["answer"] == "Fallback answer."
    assert query["rerank_error"] == "reranker unavailable"
    assert len(evidence) == 5
    assert evidence[0]["content"] == "Candidate 1"


def test_indexed_paper_renders_deepseek_question_form():
    template_dir = Path(__file__).parents[1] / "my_agent_project" / "templates"
    environment = Environment(loader=FileSystemLoader(template_dir))
    environment.globals["url_for"] = lambda _name, path: path
    template = environment.get_template("paper_detail.html")
    html = template.render(
        paper={
            "id": 1,
            "source": "arxiv",
            "status": "downloaded",
            "title": "Indexed paper",
            "authors": "Author",
            "publisher": "arXiv",
            "published": "2026-01-01",
            "doi": None,
            "page_url": "https://arxiv.org/abs/1",
            "pdf_url": "https://arxiv.org/pdf/1.pdf",
            "local_path": "paper.pdf",
            "downloaded_at": "2026-07-12T12:00:00+08:00",
            "error": None,
            "summary": "Summary",
        },
        document={
            "parse_status": "parsed",
            "page_count": 2,
            "chunk_count": 3,
            "error": None,
            "index_status": "indexed",
            "index_error": None,
            "indexed_at": "2026-07-12T12:01:00+08:00",
        },
        chunks=[],
        rag_queries=[],
        deepseek_ready=True,
        access_notice="",
    )

    assert 'action="/papers/1/index"' in html
    assert 'action="/papers/1/ask"' in html
    assert 'name="use_reranker"' in html
    assert "DeepSeek 重排" in html
    assert "DeepSeek 论文问答" in html


def test_evidence_coverage_tolerates_pdf_line_breaks():
    evidence = "including the three splits adversarial random and popular"
    candidate = "including the three splits (adver-\n sarial, random and popular)."

    assert evaluation_service.evidence_coverage(evidence, candidate) == 1.0


def test_benchmark_dataset_has_balanced_twelve_papers_and_sixty_cases():
    benchmark_path = Path(__file__).parents[1] / "benchmarks" / "chunking_baseline.csv"
    with benchmark_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    paper_counts = Counter(row["paper_title"] for row in rows)
    type_counts = Counter(row["question_type"] for row in rows)
    unique_questions = {
        (row["paper_title"], row["question"])
        for row in rows
    }

    assert len(rows) == 60
    assert len(paper_counts) == 12
    assert set(paper_counts.values()) == {5}
    assert type_counts == {
        "事实型": 12,
        "方法型": 12,
        "参数型": 12,
        "结果型": 12,
        "原因型": 12,
    }
    assert len(unique_questions) == 60
    assert all(int(row["evidence_page"]) > 0 for row in rows)


def test_fixed_english_queries_match_every_benchmark_question():
    benchmark_path = Path(__file__).parents[1] / "benchmarks" / "chunking_baseline.csv"
    query_path = Path(__file__).parents[1] / "benchmarks" / "retrieval_queries_en.csv"
    with benchmark_path.open(encoding="utf-8-sig", newline="") as file:
        benchmark_questions = {
            row["question"] for row in csv.DictReader(file)
        }

    english_queries = evaluation_service.load_english_queries(query_path)

    assert len(english_queries) == 60
    assert set(english_queries) == benchmark_questions
    assert all(query.strip() for query in english_queries.values())
    assert all(
        not any("\u4e00" <= character <= "\u9fff" for character in query)
        for query in english_queries.values()
    )


def test_evaluation_metrics_include_hit_and_mrr():
    metrics = evaluation_service._aggregate_metrics(
        [
            {
                "question_type": "事实型",
                "hit_rank": 1,
                "reciprocal_rank": 1.0,
                "best_coverage": 1.0,
                "error": None,
            },
            {
                "question_type": "事实型",
                "hit_rank": 4,
                "reciprocal_rank": 0.25,
                "best_coverage": 0.8,
                "error": None,
            },
        ],
        top_k=5,
    )

    assert metrics["hit_at_1"] == 0.5
    assert metrics["hit_at_3"] == 0.5
    assert metrics["hit_at_5"] == 1.0
    assert metrics["mrr_at_5"] == 0.625
    assert metrics["mean_best_coverage"] == 0.9


def test_evaluation_records_are_persisted(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.insert_papers("agent", [parse_arxiv_feed(ARXIV_XML)[0]])[0]
    case_id = db.upsert_evaluation_case(
        paper_id,
        "事实型",
        "Which dataset is used?",
        "CIFAR-10",
        3,
        "The experiment uses the CIFAR-10 dataset.",
    )
    run_id = db.create_evaluation_run(
        name="baseline",
        chunk_strategy="length_boundary",
        query_mode="original",
        keyword_retriever="tfidf",
        chunk_size=1200,
        chunk_overlap=150,
        embedding_model="test-embedding",
        semantic_weight=0.75,
        keyword_weight=0.25,
        top_k=5,
        case_count=1,
        reranker="deepseek_listwise",
    )
    db.save_evaluation_result(
        run_id,
        case_id,
        hit_rank=2,
        reciprocal_rank=0.5,
        best_coverage=1.0,
        retrieved=[{"rank": 2, "page_number": 3}],
    )
    db.finish_evaluation_run(run_id, "completed", metrics={"hit_at_5": 1.0})

    run = db.get_evaluation_run(run_id)
    results = db.list_evaluation_results(run_id)

    assert run is not None
    assert run["query_mode"] == "original"
    assert run["keyword_retriever"] == "tfidf"
    assert run["reranker"] == "deepseek_listwise"
    assert run["metrics"]["hit_at_5"] == 1.0
    assert results[0]["hit_rank"] == 2
    assert results[0]["retrieved"][0]["page_number"] == 3


def test_experiment_config_validates_and_derives_keyword_weight():
    config = evaluation_service.validate_experiment_config(
        "academic_section", 900, 120, 10, 0.65
    )

    assert config.chunk_strategy == "academic_section"
    assert config.query_mode == "original"
    assert config.keyword_retriever == "tfidf"
    assert config.chunk_size == 900
    assert config.chunk_overlap == 120
    assert config.top_k == 10
    assert config.semantic_weight == 0.65
    assert config.keyword_weight == 0.35

    no_prefix = evaluation_service.validate_experiment_config(
        "academic_section_no_prefix",
        900,
        120,
        10,
        0.65,
        query_mode="english",
        keyword_retriever="bm25",
    )
    assert no_prefix.chunk_strategy == "academic_section_no_prefix"
    assert no_prefix.query_mode == "english"
    assert no_prefix.keyword_retriever == "bm25"

    bilingual = evaluation_service.validate_experiment_config(
        "length_boundary",
        1200,
        150,
        5,
        0.75,
        query_mode="bilingual_rrf",
    )
    assert bilingual.query_mode == "bilingual_rrf"

    reranked = evaluation_service.validate_experiment_config(
        "length_boundary",
        1200,
        150,
        5,
        0.75,
        query_mode="english",
        reranker="deepseek_listwise",
    )
    assert reranked.reranker == "deepseek_listwise"


def test_experiment_config_rejects_unknown_query_mode():
    try:
        evaluation_service.validate_experiment_config(
            "length_boundary",
            1200,
            150,
            5,
            0.75,
            query_mode="automatic",
        )
    except ValueError as exc:
        assert "检索问题语言" in str(exc)
    else:
        raise AssertionError("unknown query mode should fail")


def test_experiment_config_rejects_unknown_keyword_retriever():
    try:
        evaluation_service.validate_experiment_config(
            "length_boundary",
            1200,
            150,
            5,
            0.75,
            keyword_retriever="keyword-search",
        )
    except ValueError as exc:
        assert "关键词检索器" in str(exc)
    else:
        raise AssertionError("unknown keyword retriever should fail")


def test_run_evaluation_uses_fixed_english_query(monkeypatch):
    original_question = "作者为什么使用分层划分？"
    english_question = "Why do the authors use stratified splitting?"
    captured_calls: list[tuple[str, str]] = []
    finished: list[tuple[int, str, dict]] = []
    monkeypatch.setattr(
        evaluation_service,
        "list_evaluation_cases",
        lambda: [
            {
                "id": 1,
                "paper_id": 3,
                "question_type": "原因型",
                "question": original_question,
                "evidence_page": 2,
                "evidence_text": "Stratification preserves the target distribution.",
            }
        ],
    )
    monkeypatch.setattr(
        evaluation_service,
        "load_english_queries",
        lambda: {original_question: english_question},
    )
    monkeypatch.setattr(
        evaluation_service,
        "retrieve_relevant_chunks",
        lambda _paper_id, query, **kwargs: captured_calls.append(
            (query, kwargs["keyword_retriever"])
        )
        or [],
    )
    monkeypatch.setattr(
        evaluation_service,
        "save_evaluation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        evaluation_service,
        "finish_evaluation_run",
        lambda run_id, status, **kwargs: finished.append((run_id, status, kwargs)),
    )

    evaluation_service.run_evaluation(
        12,
        query_mode="english",
        keyword_retriever="bm25",
    )

    assert captured_calls == [(english_question, "bm25")]
    assert finished[0][0:2] == (12, "completed")


def test_run_evaluation_persists_complete_evidence_for_generation(monkeypatch):
    evidence_tail = "The experiment uses k equals 2 because complexity grows exponentially."
    content = ("background context " * 55) + evidence_tail
    retrieval_content = "[Section: Experiments]\n" + content
    saved: list[dict] = []
    monkeypatch.setattr(
        evaluation_service,
        "list_evaluation_cases",
        lambda: [
            {
                "id": 1,
                "paper_id": 3,
                "question_type": "参数型",
                "question": "实验中的k设置为多少？",
                "evidence_page": 6,
                "evidence_text": evidence_tail,
            }
        ],
    )
    monkeypatch.setattr(
        evaluation_service,
        "retrieve_relevant_chunks",
        lambda *_args, **_kwargs: [
            {
                "chunk_id": 9,
                "page_number": 6,
                "chunk_index": 4,
                "content": content,
                "retrieval_content": retrieval_content,
                "score": 0.9,
                "semantic_score": 0.8,
                "keyword_score": 0.7,
            }
        ],
    )
    monkeypatch.setattr(
        evaluation_service,
        "save_evaluation_result",
        lambda *_args, **kwargs: saved.append(kwargs),
    )
    monkeypatch.setattr(
        evaluation_service,
        "finish_evaluation_run",
        lambda *_args, **_kwargs: None,
    )

    evaluation_service.run_evaluation(36)

    assert len(content) > 800
    assert saved[0]["hit_rank"] == 1
    stored = saved[0]["retrieved"][0]
    assert stored["content"] == content
    assert stored["retrieval_content"] == retrieval_content
    prompt = build_rag_prompt(
        "实验中的k设置为多少？",
        {"title": "Test paper", "page_url": "https://example.test/paper"},
        saved[0]["retrieved"],
    )
    assert evidence_tail in prompt


def test_reciprocal_rank_fusion_promotes_candidates_found_by_both_routes():
    fused = reciprocal_rank_fusion(
        {
            "original": [
                {"chunk_id": 1, "content": "中文第一"},
                {"chunk_id": 2, "content": "双路命中"},
            ],
            "english": [
                {"chunk_id": 3, "content": "英文第一"},
                {"chunk_id": 2, "content": "双路命中"},
            ],
        },
        top_k=3,
    )

    assert [item["chunk_id"] for item in fused] == [2, 1, 3]
    assert fused[0]["fusion_ranks"] == {"original": 2, "english": 2}
    assert fused[0]["score"] > fused[1]["score"]


def test_deepseek_reranker_validates_and_caches_complete_ranking(monkeypatch):
    cache_stats: dict[str, int] = {}
    saved: list[dict] = []
    candidates = [
        {"chunk_id": 10, "content": "less relevant"},
        {"chunk_id": 20, "content": "direct answer"},
        {"chunk_id": 30, "content": "background"},
    ]
    monkeypatch.setattr(rag_service, "get_cached_llm_response", lambda *_args: None)
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: ({"ranking": [2, 1, 3]}, "test-model"),
    )
    monkeypatch.setattr(
        rag_service,
        "upsert_cached_llm_response",
        lambda _namespace, _input_hash, response: saved.append(response),
    )

    reranked = rag_service.rerank_candidates_with_deepseek(
        "Which candidate answers the question?",
        candidates,
        top_k=2,
        cache_stats=cache_stats,
    )

    assert [item["chunk_id"] for item in reranked] == [20, 10]
    assert reranked[0]["retrieval_rank"] == 2
    assert reranked[0]["rerank_rank"] == 1
    assert cache_stats == {"rerank_cache_misses": 1}
    assert saved == [{"ranking": [2, 1, 3]}]


def test_deepseek_reranker_rejects_incomplete_ranking(monkeypatch):
    monkeypatch.setattr(rag_service, "get_cached_llm_response", lambda *_args: None)
    monkeypatch.setattr(
        rag_service,
        "ask_deepseek_json",
        lambda *_args, **_kwargs: ({"ranking": [1, 1]}, "test-model"),
    )

    try:
        rag_service.rerank_candidates_with_deepseek(
            "question",
            [{"chunk_id": 1, "content": "a"}, {"chunk_id": 2, "content": "b"}],
            top_k=2,
        )
    except ValueError as exc:
        assert "全部候选 ID" in str(exc)
    else:
        raise AssertionError("incomplete reranker output should fail")


def test_run_evaluation_uses_bilingual_rrf_queries(monkeypatch):
    original_question = "作者为什么使用分层划分？"
    english_question = "Why do the authors use stratified splitting?"
    captured_calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        evaluation_service,
        "list_evaluation_cases",
        lambda: [
            {
                "id": 1,
                "paper_id": 3,
                "question_type": "原因型",
                "question": original_question,
                "evidence_page": 2,
                "evidence_text": "Stratification preserves the target distribution.",
            }
        ],
    )
    monkeypatch.setattr(
        evaluation_service,
        "load_english_queries",
        lambda: {original_question: english_question},
    )
    monkeypatch.setattr(
        evaluation_service,
        "retrieve_bilingual_rrf_chunks",
        lambda paper_id, original, english, **kwargs: captured_calls.append(
            (original, english, kwargs["top_k"])
        )
        or [],
    )
    monkeypatch.setattr(
        evaluation_service,
        "save_evaluation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        evaluation_service,
        "finish_evaluation_run",
        lambda *_args, **_kwargs: None,
    )

    evaluation_service.run_evaluation(32, query_mode="bilingual_rrf", top_k=5)

    assert captured_calls == [(original_question, english_question, 5)]


def test_run_evaluation_reranks_top_twenty_english_candidates(monkeypatch):
    original_question = "数据集如何划分？"
    english_question = "How is the dataset split?"
    retrieval_calls: list[tuple[str, int]] = []
    rerank_calls: list[tuple[str, int, int]] = []
    candidates = [
        {
            "chunk_id": index,
            "page_number": 2,
            "chunk_index": index,
            "content": f"candidate {index}",
            "score": 0.5,
            "semantic_score": 0.5,
            "keyword_score": 0.5,
        }
        for index in range(1, 21)
    ]
    monkeypatch.setattr(
        evaluation_service,
        "list_evaluation_cases",
        lambda: [
            {
                "id": 1,
                "paper_id": 3,
                "question_type": "参数型",
                "question": original_question,
                "evidence_page": 2,
                "evidence_text": "The dataset is split 80/20.",
            }
        ],
    )
    monkeypatch.setattr(
        evaluation_service,
        "load_english_queries",
        lambda: {original_question: english_question},
    )
    monkeypatch.setattr(
        evaluation_service,
        "retrieve_relevant_chunks",
        lambda _paper_id, query, **kwargs: retrieval_calls.append(
            (query, kwargs["top_k"])
        )
        or candidates,
    )
    monkeypatch.setattr(
        evaluation_service,
        "rerank_candidates_with_deepseek",
        lambda query, items, **kwargs: rerank_calls.append(
            (query, len(items), kwargs["top_k"])
        )
        or list(items)[: kwargs["top_k"]],
    )
    monkeypatch.setattr(
        evaluation_service,
        "save_evaluation_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        evaluation_service,
        "finish_evaluation_run",
        lambda *_args, **_kwargs: None,
    )

    evaluation_service.run_evaluation(
        34,
        query_mode="english",
        reranker="deepseek_listwise",
        top_k=5,
    )

    assert retrieval_calls == [(english_question, 20)]
    assert rerank_calls == [(english_question, 20, 5)]


def test_experiment_config_rejects_overlap_not_smaller_than_chunk():
    try:
        evaluation_service.validate_experiment_config(
            "length_boundary", 600, 600, 5, 0.75
        )
    except ValueError as exc:
        assert "chunk_overlap" in str(exc)
    else:
        raise AssertionError("invalid overlap should fail")


def test_configured_experiment_uses_cache_aware_pipeline(monkeypatch):
    parsed: list[tuple[int, str, int, int]] = []
    indexed: list[int] = []
    evaluated: list[dict] = []
    monkeypatch.setattr(
        evaluation_service,
        "list_evaluation_cases",
        lambda: [{"paper_id": 2}, {"paper_id": 1}, {"paper_id": 2}],
    )
    monkeypatch.setattr(
        evaluation_service,
        "parse_paper_pdf",
        lambda paper_id, *, strategy, chunk_size, overlap, cache_stats: parsed.append(
            (paper_id, strategy, chunk_size, overlap)
        ),
    )
    monkeypatch.setattr(
        evaluation_service,
        "cache_active_chunk_embeddings",
        lambda _paper_id, _cache_stats: 0,
    )
    reuse_checks = iter([False, True, False, True])
    monkeypatch.setattr(
        evaluation_service,
        "document_index_is_reusable",
        lambda _document: next(reuse_checks),
    )
    monkeypatch.setattr(
        evaluation_service,
        "get_document",
        lambda _paper_id: {
            "parse_status": "parsed",
            "index_status": "indexed",
            "chunk_count": 7,
            "error": None,
            "index_error": None,
        },
    )
    monkeypatch.setattr(
        evaluation_service,
        "index_paper_chunks",
        lambda paper_id, _cache_stats: indexed.append(paper_id),
    )
    monkeypatch.setattr(
        evaluation_service,
        "list_paper_chunks",
        lambda paper_id, limit: [
            {"content": f"[Section: Paper {paper_id} > Methods]\nText"}
        ],
    )
    monkeypatch.setattr(
        evaluation_service,
        "run_evaluation",
        lambda run_id, **kwargs: evaluated.append({"run_id": run_id, **kwargs}),
    )
    config = evaluation_service.ExperimentConfig(
        "academic_section", "english", "bm25", 800, 100, 10, 0.7, 0.3
    )

    evaluation_service.run_configured_experiment(9, config)

    assert parsed == [
        (1, "academic_section", 800, 100),
        (2, "academic_section", 800, 100),
    ]
    assert indexed == [1, 2]
    assert evaluated[0]["run_id"] == 9
    assert evaluated[0]["query_mode"] == "english"
    assert evaluated[0]["keyword_retriever"] == "bm25"
    assert evaluated[0]["top_k"] == 10
    assert evaluated[0]["extra_metrics"]["total_chunk_count"] == 14
    assert evaluated[0]["extra_metrics"]["detected_section_count"] == 2


def test_configured_experiment_rejects_embedding_fallback(monkeypatch):
    finished: list[tuple[int, str, str | None]] = []
    monkeypatch.setattr(
        evaluation_service,
        "list_evaluation_cases",
        lambda: [{"paper_id": 1}],
    )
    monkeypatch.setattr(
        evaluation_service,
        "cache_active_chunk_embeddings",
        lambda _paper_id, _cache_stats: 0,
    )
    monkeypatch.setattr(
        evaluation_service,
        "parse_paper_pdf",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        evaluation_service,
        "get_document",
        lambda _paper_id: {
            "parse_status": "parsed",
            "index_status": "indexed",
            "chunk_count": 1,
            "error": None,
            "index_error": "Seed unavailable; TF-IDF fallback",
        },
    )
    monkeypatch.setattr(
        evaluation_service,
        "list_paper_chunks",
        lambda _paper_id, limit: [{"content": "text"}],
    )
    monkeypatch.setattr(
        evaluation_service,
        "document_index_is_reusable",
        lambda _document: False,
    )
    monkeypatch.setattr(
        evaluation_service,
        "index_paper_chunks",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        evaluation_service,
        "finish_evaluation_run",
        lambda run_id, status, error=None, **_kwargs: finished.append(
            (run_id, status, error)
        ),
    )
    config = evaluation_service.ExperimentConfig(
        "length_boundary",
        "english",
        "tfidf",
        1200,
        150,
        5,
        0.75,
        0.25,
    )

    evaluation_service.run_configured_experiment(20, config)

    assert finished[0][0:2] == (20, "failed")
    assert "统一的实验索引" in finished[0][2]


def test_evaluation_page_exposes_chart_metrics():
    template_dir = Path(__file__).parents[1] / "my_agent_project" / "templates"
    environment = Environment(loader=FileSystemLoader(template_dir))
    environment.globals["url_for"] = lambda _name, path: path
    template = environment.get_template("evaluation.html")
    metrics = {
        "hit_at_1": 0.6,
        "hit_at_3": 0.8,
        "hit_at_5": 1.0,
        "mrr_at_5": 0.75,
        "mean_best_coverage": 0.9,
        "total_duration_seconds": 2.5,
        "cache_stats": {
            "pdf_page_hits": 20,
            "chunk_embedding_hits": 10,
            "query_embedding_hits": 5,
            "index_document_hits": 2,
        },
    }
    run = {
        "id": 3,
        "name": "baseline",
        "status": "completed",
        "chunk_strategy": "length_boundary",
        "query_mode": "original",
        "keyword_retriever": "tfidf",
        "reranker": "none",
        "chunk_size": 1200,
        "chunk_overlap": 150,
        "embedding_model": "test-embedding",
        "semantic_weight": 0.75,
        "keyword_weight": 0.25,
        "top_k": 5,
        "error": None,
        "metrics": metrics,
        "case_count": 1,
        "completed_at": "2026-07-13T12:00:00+08:00",
    }

    html = template.render(cases=[], runs=[run], selected_run=run, results=[])

    assert 'id="top-k-chart"' in html
    assert 'id="run-history-chart"' in html
    assert 'data-hit1="0.6"' in html
    assert 'data-evaluation-run="3"' in html
    assert 'name="chunk_size"' in html
    assert 'name="chunk_overlap"' in html
    assert 'name="chunk_overlap" value="150" min="0" max="2999" step="10"' in html
    assert 'name="semantic_weight"' in html
    assert 'name="query_mode"' in html
    assert 'name="keyword_retriever"' in html
    assert 'name="reranker"' in html
    assert '<option value="english">固定英文 english</option>' in html
    assert '<option value="bilingual_rrf">中英文双路 RRF bilingual_rrf</option>' in html
    assert '<option value="bm25">BM25 实验</option>' in html
    assert '<option value="deepseek_listwise">DeepSeek Top-20 列表重排</option>' in html
    assert '<option value="academic_section">' in html
    assert '<option value="academic_section_no_prefix">' in html
    assert '<option value="paper_section_sentence">' in html
    assert '<option value="paper_parent_child">' in html
    assert '<option value="paper_table_context">' in html
    assert '<option value="paper_semantic_breakpoint_global">' in html
    assert "2.50 秒" in html
    assert "20 命中 / 0 新增" in html


def test_generation_evaluation_page_renders_quality_metrics():
    template_dir = Path(__file__).parents[1] / "my_agent_project" / "templates"
    environment = Environment(loader=FileSystemLoader(template_dir))
    environment.globals["url_for"] = lambda _name, path: path
    template = environment.get_template("generation_evaluation.html")
    metrics = {
        "faithfulness": 0.75,
        "answer_relevance": 0.8,
        "reference_correctness": 0.7,
        "citation_correctness": 0.9,
        "total_duration_seconds": 3.5,
        "cache_stats": {
            "generation_cache_hits": 1,
            "generation_cache_misses": 0,
            "judge_cache_hits": 1,
            "judge_cache_misses": 0,
        },
    }
    run = {
        "id": 2,
        "name": "generation smoke",
        "status": "completed",
        "retrieval_run_id": 35,
        "retrieval_run_name": "reranked retrieval",
        "generator_model": "deepseek-test",
        "judge_model": "deepseek-test",
        "prompt_version": "answer-v1+judge-v1",
        "metrics": metrics,
        "error": None,
        "completed_at": "2026-07-30T12:00:00+08:00",
    }
    result = {
        "question_type": "事实型",
        "paper_title": "Test paper",
        "question": "Which dataset is used?",
        "answer": "The paper uses CIFAR-10 [第 3 页].",
        "faithfulness": 1.0,
        "answer_relevance": 1.0,
        "reference_correctness": 1.0,
        "citation_correctness": 1.0,
        "judge": {
            "claims": [
                {
                    "text": "The paper uses CIFAR-10.",
                    "supported": True,
                    "cited_pages": [3],
                }
            ]
        },
        "error": None,
    }

    html = template.render(
        retrieval_runs=[{"id": 35, "name": "reranked retrieval"}],
        runs=[run],
        selected_run=run,
        results=[result],
    )

    assert 'action="/generation-evaluation/run"' in html
    assert 'data-evaluation-running' not in html
    assert "75.0%" in html
    assert "80.0%" in html
    assert "70.0%" in html
    assert "90.0%" in html
    assert "1 命中 / 0 新增" in html
    assert "The paper uses CIFAR-10" in html

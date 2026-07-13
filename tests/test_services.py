import csv
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from my_agent_project import db
from my_agent_project import evaluation_service
from my_agent_project import model_service
from my_agent_project import pdf_service
from my_agent_project import rag_service

from my_agent_project.download_service import can_attempt_download, sanitize_filename
from my_agent_project.origin_service import build_origin_chart_tables
from my_agent_project.pdf_service import (
    detect_section_heading,
    split_academic_page_text,
    split_page_text,
)
from my_agent_project.rag_service import (
    VECTOR_MODEL,
    build_access_notice,
    build_rag_prompt,
    index_paper_chunks,
    retrieve_relevant_chunks,
)
from my_agent_project.schemas import PaperChunk
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
    assert "CIFAR-10" in query["answer"]
    evidence = db.list_rag_queries(paper_id)[0]["evidence"]
    assert evidence[0]["page_number"] == 3


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
        chunk_size=1200,
        chunk_overlap=150,
        embedding_model="test-embedding",
        semantic_weight=0.75,
        keyword_weight=0.25,
        top_k=5,
        case_count=1,
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
    assert run["metrics"]["hit_at_5"] == 1.0
    assert results[0]["hit_rank"] == 2
    assert results[0]["retrieved"][0]["page_number"] == 3


def test_experiment_config_validates_and_derives_keyword_weight():
    config = evaluation_service.validate_experiment_config(
        "academic_section", 900, 120, 10, 0.65
    )

    assert config.chunk_strategy == "academic_section"
    assert config.chunk_size == 900
    assert config.chunk_overlap == 120
    assert config.top_k == 10
    assert config.semantic_weight == 0.65
    assert config.keyword_weight == 0.35


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
    monkeypatch.setattr(
        evaluation_service,
        "document_index_is_reusable",
        lambda _document: False,
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
        "academic_section", 800, 100, 10, 0.7, 0.3
    )

    evaluation_service.run_configured_experiment(9, config)

    assert parsed == [
        (1, "academic_section", 800, 100),
        (2, "academic_section", 800, 100),
    ]
    assert indexed == [1, 2]
    assert evaluated[0]["run_id"] == 9
    assert evaluated[0]["top_k"] == 10
    assert evaluated[0]["extra_metrics"]["total_chunk_count"] == 14
    assert evaluated[0]["extra_metrics"]["detected_section_count"] == 2


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
    assert '<option value="academic_section">' in html
    assert "2.50 秒" in html
    assert "20 命中 / 0 新增" in html

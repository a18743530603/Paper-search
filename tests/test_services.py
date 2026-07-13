from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from my_agent_project import db
from my_agent_project import model_service
from my_agent_project import rag_service

from my_agent_project.download_service import can_attempt_download, sanitize_filename
from my_agent_project.origin_service import build_origin_chart_tables
from my_agent_project.pdf_service import split_page_text
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

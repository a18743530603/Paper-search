import io
import json
import zipfile

import httpx

from my_agent_project import db, mineru_service, pdf_service


def _result_zip(content_list: list[dict], markdown: str = "# Paper") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "paper/paper_content_list.json",
            json.dumps(content_list, ensure_ascii=False),
        )
        archive.writestr("paper/full.md", markdown)
    return buffer.getvalue()


def test_mineru_precision_api_upload_poll_and_cache(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    content_list = [
        {
            "type": "text",
            "text": "1 Introduction",
            "text_level": 1,
            "page_idx": 0,
            "bbox": [10, 20, 900, 60],
        },
        {
            "type": "text",
            "text": "The proposed method uses robust evidence retrieval.",
            "page_idx": 0,
            "bbox": [10, 80, 900, 140],
        },
    ]
    zip_bytes = _result_zip(content_list)
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.method == "POST":
            assert request.headers["Authorization"] == "Bearer test-token"
            payload = json.loads(request.content)
            assert payload["model_version"] == "vlm"
            assert payload["files"][0]["data_id"] == "paper_1_hash"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.example/paper"],
                    },
                },
            )
        if request.method == "PUT":
            assert request.content == b"%PDF-fake"
            return httpx.Response(200)
        if request.url.host == "download.example":
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-1",
                    "extract_result": [
                        {
                            "data_id": "paper_1_hash",
                            "state": "done",
                            "full_zip_url": "https://download.example/result.zip",
                        }
                    ],
                },
            },
        )

    monkeypatch.setattr(mineru_service, "MINERU_API_TOKEN", "test-token")
    monkeypatch.setattr(mineru_service, "MINERU_MODEL_VERSION", "vlm")
    monkeypatch.setattr(mineru_service, "MINERU_RESULTS_DIR", tmp_path / "results")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    progress: list[tuple[str, str | None]] = []

    result = mineru_service.parse_pdf_with_mineru(
        pdf_path,
        source_hash="hash",
        data_id="paper_1_hash",
        on_progress=lambda state, batch_id, _done, _total: progress.append(
            (state, batch_id)
        ),
        client=client,
    )

    assert result.batch_id == "batch-1"
    assert result.content_list == content_list
    assert (result.result_dir / "content_list.json").is_file()
    assert (result.result_dir / "full.md").read_text(encoding="utf-8") == "# Paper"
    assert progress == [
        ("uploading", "batch-1"),
        ("pending", "batch-1"),
        ("done", "batch-1"),
    ]
    assert [method for method, _url in requests] == ["POST", "PUT", "GET", "GET"]


def test_mineru_structured_chunks_keep_sections_types_and_pages():
    content_list = [
        {
            "type": "header",
            "text": "Journal header",
            "page_idx": 0,
            "bbox": [0, 0, 1000, 30],
        },
        {
            "type": "text",
            "text": "1 Methods",
            "text_level": 1,
            "page_idx": 0,
            "bbox": [20, 40, 900, 70],
        },
        {
            "type": "text",
            "text": "1.1 Training details",
            "text_level": 2,
            "page_idx": 0,
            "bbox": [20, 80, 900, 110],
        },
        {
            "type": "text",
            "text": "AdamW is used for five epochs.",
            "page_idx": 0,
            "bbox": [20, 120, 900, 180],
        },
        {
            "type": "table",
            "table_caption": ["Table 1. Main results"],
            "table_body": "<table><tr><td>Accuracy</td><td>91.2</td></tr></table>",
            "page_idx": 1,
            "bbox": [20, 200, 900, 600],
        },
        {
            "type": "image",
            "img_caption": ["Figure 1. Model architecture"],
            "img_footnote": ["The arrows indicate data flow."],
            "page_idx": 1,
        },
        {
            "type": "text",
            "text": "References",
            "text_level": 1,
            "page_idx": 2,
        },
        {
            "type": "list",
            "sub_type": "ref_text",
            "list_items": ["A. Author, 2025"],
            "page_idx": 2,
        },
        {
            "type": "text",
            "text": "A. Author et al.",
            "text_level": 2,
            "page_idx": 2,
        },
        {
            "type": "image",
            "img_caption": ["Figure in references must stay excluded"],
            "page_idx": 2,
        },
        {
            "type": "text",
            "text": "Appendix A",
            "text_level": 1,
            "page_idx": 3,
        },
        {
            "type": "equation",
            "text": "lambda = 1.0",
            "page_idx": 3,
            "bbox": [100, 200, 800, 260],
        },
    ]

    page_count, chunks, stats = mineru_service.build_structured_chunks(content_list)

    assert page_count == 4
    assert [chunk.content_type for chunk in chunks] == [
        "text",
        "table",
        "image",
        "equation",
    ]
    assert chunks[0].section_title == "1 Methods > 1.1 Training details"
    assert chunks[0].page_number == 1
    assert chunks[0].bbox == (20.0, 120.0, 900.0, 180.0)
    assert chunks[1].page_number == 2
    assert "Table 1" in chunks[1].content
    assert "Model architecture" in chunks[2].content
    assert chunks[3].section_title == "Appendix A"
    assert all("references" not in chunk.content.lower() for chunk in chunks)
    assert stats["section_count"] == 3
    assert stats["content_type_counts"] == {
        "text": 1,
        "table": 1,
        "image": 1,
        "equation": 1,
    }


def test_default_pdf_parse_uses_cached_mineru_structure(tmp_path, monkeypatch):
    database_path = tmp_path / "metadata.db"
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-cached")
    monkeypatch.setattr(db, "DATABASE_PATH", database_path)
    db.init_db()
    paper_id = db.register_local_paper("MinerU cached paper", pdf_path)
    content_list = [
        {
            "type": "text",
            "text": "2 Results",
            "text_level": 1,
            "page_idx": 1,
        },
        {
            "type": "text",
            "text": "The method improves Hit@5.",
            "page_idx": 1,
            "bbox": [10, 20, 900, 80],
        },
    ]
    monkeypatch.setattr(
        pdf_service,
        "load_cached_content_list",
        lambda _source_hash, _model: content_list,
    )

    result = pdf_service.parse_paper_pdf(paper_id)

    assert result["parser_backend"] == "mineru_cloud"
    document = db.get_document(paper_id)
    assert document["parse_status"] == "parsed"
    assert document["chunk_strategy"] == "mineru_structured"
    assert document["parser_backend"] == "mineru_cloud"
    assert document["parser_model"] == pdf_service.MINERU_MODEL_VERSION
    chunks = db.list_paper_chunks(paper_id)
    assert len(chunks) == 1
    assert chunks[0]["section_title"] == "2 Results"
    assert chunks[0]["content_type"] == "text"
    assert json.loads(chunks[0]["bbox_json"]) == [10.0, 20.0, 900.0, 80.0]


def test_mineru_requires_token_before_remote_upload(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    monkeypatch.setattr(mineru_service, "MINERU_API_TOKEN", "")

    try:
        mineru_service.parse_pdf_with_mineru(
            pdf_path,
            source_hash="hash",
            data_id="paper_1_hash",
        )
    except mineru_service.MinerUError as exc:
        assert "MINERU_API_TOKEN" in str(exc)
    else:
        raise AssertionError("missing MinerU token should fail before upload")

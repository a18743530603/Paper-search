import os

os.environ["PYTHONIOENCODING"] = "utf-8"

import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .model_service import enhance_query, is_deepseek_configured
from .config import BASE_DIR, MAX_RESULTS, configure_utf8_stdio, ensure_runtime_dirs
from .db import (
    clear_paper_history,
    create_rag_query,
    export_papers_csv,
    export_papers_json,
    get_document,
    get_evaluation_run,
    get_or_create_document,
    get_paper,
    init_db,
    insert_papers,
    list_evaluation_cases,
    list_evaluation_results,
    list_evaluation_runs,
    list_paper_chunks,
    list_paper_statuses,
    list_papers,
    list_rag_queries,
    update_download_status,
)
from .download_service import can_attempt_download, download_paper
from .evaluation_service import (
    create_experiment_run,
    import_benchmark_cases,
    prepare_benchmark,
    run_configured_experiment,
)
from .origin_service import create_origin_outputs
from .pdf_service import parse_paper_pdf
from .rag_service import (
    answer_rag_query,
    build_access_notice,
    index_paper_chunks,
)
from .schemas import INDEX_INDEXED, PARSE_PARSED, STATUS_DOWNLOADING
from .search_service import search_all


configure_utf8_stdio()
ensure_runtime_dirs()
init_db()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Paper Hunter", version="0.5.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"max_results": MAX_RESULTS},
    )


@app.post("/search")
def search(
    request: Request,
    query: str = Form(...),
    max_results: int = Form(MAX_RESULTS),
):
    query = query.strip()
    if not query:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "max_results": MAX_RESULTS,
                "error": "请输入关键词。",
            },
            status_code=400,
        )

    max_results = max(1, min(max_results, 50))
    enhanced_query = enhance_query(query)
    candidates = search_all(enhanced_query, max_results)
    paper_ids = insert_papers(query, candidates)

    papers = [get_paper(paper_id) for paper_id in paper_ids]
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "query": query,
            "enhanced_query": enhanced_query,
            "papers": [paper for paper in papers if paper is not None],
        },
    )


@app.get("/papers")
def papers(request: Request):
    return templates.TemplateResponse(
        request,
        "papers.html",
        {"papers": list_papers()},
    )


@app.post("/papers/clear")
def clear_history():
    clear_paper_history()
    return RedirectResponse(url="/papers", status_code=303)


@app.get("/api/papers/statuses")
def paper_statuses():
    return {"papers": list_paper_statuses()}


@app.get("/papers/{paper_id}")
def paper_detail(request: Request, paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    document = get_document(paper_id)
    chunks = list_paper_chunks(paper_id, limit=8)
    rag_queries = list_rag_queries(paper_id)
    return templates.TemplateResponse(
        request,
        "paper_detail.html",
        {
            "paper": paper,
            "document": document,
            "chunks": chunks,
            "rag_queries": rag_queries,
            "deepseek_ready": is_deepseek_configured(),
            "access_notice": build_access_notice(dict(paper)),
        },
    )


@app.post("/papers/{paper_id}/parse")
def parse_paper(paper_id: int, background_tasks: BackgroundTasks):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper["local_path"]:
        raise HTTPException(status_code=409, detail="Paper PDF is not downloaded")
    get_or_create_document(paper_id)
    background_tasks.add_task(parse_paper_pdf, paper_id)
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/index")
def index_paper(paper_id: int, background_tasks: BackgroundTasks):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    document = get_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        raise HTTPException(status_code=409, detail="Paper is not parsed")
    background_tasks.add_task(index_paper_chunks, paper_id)
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/ask")
def ask_paper(
    paper_id: int,
    background_tasks: BackgroundTasks,
    question: str = Form(...),
):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    document = get_document(paper_id)
    if document is None or document["index_status"] != INDEX_INDEXED:
        raise HTTPException(status_code=409, detail="Paper is not indexed")
    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")
    if len(question) > 1000:
        raise HTTPException(status_code=400, detail="Question is too long")
    query_id = create_rag_query(paper_id, question)
    background_tasks.add_task(answer_rag_query, query_id)
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/retry")
def retry_download(paper_id: int, background_tasks: BackgroundTasks):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not can_attempt_download(paper["source"], paper["pdf_url"]):
        raise HTTPException(status_code=409, detail="Paper has no downloadable PDF")
    update_download_status(paper_id, STATUS_DOWNLOADING, error=None)
    background_tasks.add_task(download_paper, paper_id)
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/download")
def start_download(paper_id: int, background_tasks: BackgroundTasks):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not can_attempt_download(paper["source"], paper["pdf_url"]):
        raise HTTPException(status_code=409, detail="Paper has no downloadable PDF")
    if paper["status"] == STATUS_DOWNLOADING:
        return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)
    update_download_status(paper_id, STATUS_DOWNLOADING, error=None)
    background_tasks.add_task(download_paper, paper_id)
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.get("/evaluation")
def evaluation_dashboard(request: Request):
    runs = list_evaluation_runs()
    latest_run = runs[0] if runs else None
    return templates.TemplateResponse(
        request,
        "evaluation.html",
        {
            "cases": list_evaluation_cases(),
            "runs": runs,
            "selected_run": latest_run,
            "results": (
                list_evaluation_results(latest_run["id"])
                if latest_run and latest_run["status"] == "completed"
                else []
            ),
        },
    )


@app.post("/evaluation/prepare")
def prepare_evaluation(background_tasks: BackgroundTasks):
    import_benchmark_cases()
    background_tasks.add_task(prepare_benchmark)
    return RedirectResponse(url="/evaluation", status_code=303)


@app.post("/evaluation/run")
def start_evaluation(
    background_tasks: BackgroundTasks,
    experiment_name: str = Form("固定边界分块实验"),
    chunk_size: int = Form(1200),
    chunk_overlap: int = Form(150),
    top_k: int = Form(5),
    semantic_weight: float = Form(0.75),
):
    try:
        run_id, config = create_experiment_run(
            name=experiment_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            top_k=top_k,
            semantic_weight=semantic_weight,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(run_configured_experiment, run_id, config)
    return RedirectResponse(url=f"/evaluation/runs/{run_id}", status_code=303)


@app.get("/evaluation/runs/{run_id}")
def evaluation_run_detail(request: Request, run_id: int):
    selected_run = get_evaluation_run(run_id)
    if selected_run is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return templates.TemplateResponse(
        request,
        "evaluation.html",
        {
            "cases": list_evaluation_cases(),
            "runs": list_evaluation_runs(),
            "selected_run": selected_run,
            "results": (
                list_evaluation_results(run_id)
                if selected_run["status"] == "completed"
                else []
            ),
        },
    )


@app.get("/origin")
def origin_dashboard(request: Request):
    output = create_origin_outputs()
    image_urls = [
        f"/origin/images/{image_path.name}"
        for image_path in output.image_paths
    ]
    return templates.TemplateResponse(
        request,
        "origin.html",
        {"output": output, "image_urls": image_urls},
    )


@app.get("/origin/summary.csv")
def origin_summary_csv():
    output = create_origin_outputs(launch_origin=False)
    return FileResponse(
        output.summary_csv,
        filename="paper_hunter_origin_summary.csv",
        media_type="text/csv",
    )


@app.get("/origin/images/{filename}")
def origin_image(filename: str):
    from .config import ORIGIN_EXPORTS_DIR

    path = ORIGIN_EXPORTS_DIR / filename
    if path.parent != ORIGIN_EXPORTS_DIR or path.suffix.lower() != ".png" or not path.exists():
        raise HTTPException(status_code=404, detail="Origin image not found")
    return FileResponse(path, media_type="image/png")


@app.get("/export.csv")
def export_csv():
    return FileResponse(export_papers_csv(), filename="papers.csv", media_type="text/csv")


@app.get("/export.json")
def export_json():
    return FileResponse(
        export_papers_json(),
        filename="papers.json",
        media_type="application/json",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("my_agent_project.main:app", host="127.0.0.1", port=8000, reload=True)

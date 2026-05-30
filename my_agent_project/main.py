import os

os.environ["PYTHONIOENCODING"] = "utf-8"

import sys
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .agent_service import enhance_query
from .config import BASE_DIR, MAX_RESULTS, configure_utf8_stdio, ensure_runtime_dirs
from .db import (
    export_papers_csv,
    export_papers_json,
    get_paper,
    init_db,
    insert_papers,
    list_papers,
)
from .download_service import download_paper
from .schemas import STATUS_DOWNLOADING
from .search_service import search_all


configure_utf8_stdio()
ensure_runtime_dirs()
init_db()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Paper Hunter", version="0.1.0")
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
    background_tasks: BackgroundTasks,
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

    for paper_id, paper in zip(paper_ids, candidates):
        if paper.status == STATUS_DOWNLOADING:
            background_tasks.add_task(download_paper, paper_id)

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


@app.get("/papers/{paper_id}")
def paper_detail(request: Request, paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return templates.TemplateResponse(
        request,
        "paper_detail.html",
        {"paper": paper},
    )


@app.post("/papers/{paper_id}/retry")
def retry_download(paper_id: int, background_tasks: BackgroundTasks):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    background_tasks.add_task(download_paper, paper_id)
    return RedirectResponse(url="/papers", status_code=303)


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

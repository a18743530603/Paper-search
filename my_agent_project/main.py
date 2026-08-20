import os

os.environ["PYTHONIOENCODING"] = "utf-8"

import sys
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .model_service import enhance_query, is_deepseek_configured
from .agent_metrics_service import build_agent_metrics
from .config import BASE_DIR, MAX_RESULTS, configure_utf8_stdio, ensure_runtime_dirs
from .db import (
    attach_agent_traces_to_task,
    cancel_research_task,
    clear_paper_history,
    create_research_task,
    enqueue_download_job,
    enqueue_index_job,
    enqueue_parse_job,
    enqueue_paper_preparation_job,
    enqueue_rag_answer_job,
    enqueue_research_report_job,
    enqueue_research_search_job,
    export_papers_csv,
    export_papers_json,
    get_document,
    get_evaluation_run,
    get_generation_evaluation_run,
    get_paper,
    get_research_task,
    get_research_report,
    init_db,
    insert_papers,
    list_evaluation_cases,
    list_evaluation_results,
    list_evaluation_runs,
    list_agent_traces,
    list_generation_evaluation_results,
    list_generation_evaluation_runs,
    list_paper_chunks,
    list_paper_statuses,
    list_papers,
    list_research_tasks,
    list_research_tool_calls,
    list_rag_queries,
    select_research_task_papers,
    validate_research_task_paper_selection,
)
from .download_service import can_attempt_download
from .evaluation_service import (
    create_experiment_run,
    import_benchmark_cases,
    prepare_benchmark,
    run_configured_experiment,
)
from .generation_evaluation_service import (
    create_generation_run,
    run_generation_evaluation,
)
from .origin_service import create_origin_outputs
from .paper_workflow import (
    get_paper_preparation_status,
    paper_preparation_status,
)
from .rag_service import (
    RERANKER_DEEPSEEK_LISTWISE,
    RERANKER_NONE,
    build_access_notice,
)
from .research_agent import (
    SEARCH_MODE_RESEARCH_AGENT,
    SEARCH_MODE_STANDARD,
)
from .research_checkpoint import (
    delete_research_checkpoint,
    research_checkpoint_info,
    resume_paper_selection,
    resume_plan_confirmation,
    start_checkpointed_research,
)
from .schemas import (
    INDEX_INDEXED,
    MAX_RESEARCH_SELECTED_PAPERS,
    PARSE_PARSED,
    RESEARCH_TASK_AWAITING,
    STATUS_AVAILABLE,
    STATUS_FAILED,
)
from .search_service import search_all


configure_utf8_stdio()
ensure_runtime_dirs()
init_db()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Paper Hunter", version="0.13.1")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def paper_view(paper):
    item = dict(paper)
    document = get_document(int(item["id"]))
    item["preparation_status"] = paper_preparation_status(
        item,
        dict(document) if document else None,
    )
    return item


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "max_results": MAX_RESULTS,
            "search_mode": SEARCH_MODE_STANDARD,
        },
    )


@app.post("/search")
def search(
    request: Request,
    query: str = Form(...),
    max_results: int = Form(MAX_RESULTS),
    search_mode: str = Form(SEARCH_MODE_STANDARD),
):
    query = query.strip()
    if not query:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "max_results": MAX_RESULTS,
                "error": "请输入关键词。",
                "query": query,
                "search_mode": search_mode,
            },
            status_code=400,
        )

    max_results = max(1, min(max_results, 50))
    if search_mode == SEARCH_MODE_RESEARCH_AGENT:
        thread_id = f"research-{uuid4()}"
        research_plan = start_checkpointed_research(
            thread_id,
            query,
            max_results,
        )
        task_id = create_research_task(
            question=query,
            max_results=max_results,
            plan_goal=research_plan.goal,
            plan_queries=research_plan.queries,
            plan_rationale=research_plan.rationale,
            plan_model=research_plan.model,
            planner_status=research_plan.status,
            planner_error=research_plan.error,
            status=RESEARCH_TASK_AWAITING,
            checkpoint_thread_id=thread_id,
        )
        attach_agent_traces_to_task(thread_id, task_id)
        return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)

    search_mode = SEARCH_MODE_STANDARD
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
            "search_mode": search_mode,
            "research_plan": None,
            "papers": [paper_view(paper) for paper in papers if paper is not None],
        },
    )


@app.get("/research-tasks")
def research_tasks(request: Request):
    return templates.TemplateResponse(
        request,
        "research_tasks.html",
        {"tasks": list_research_tasks()},
    )


@app.get("/research-tasks/{task_id}")
def research_task_detail(request: Request, task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    papers = [get_paper(int(paper_id)) for paper_id in task["paper_ids"]]
    selected_papers = [
        get_paper(int(paper_id)) for paper_id in task["selected_paper_ids"]
    ]
    return templates.TemplateResponse(
        request,
        "research_task.html",
        {
            "task": task,
            "papers": [paper_view(paper) for paper in papers if paper is not None],
            "selected_papers": [
                paper_view(paper) for paper in selected_papers if paper is not None
            ],
            "tool_calls": list_research_tool_calls(task_id),
            "report": get_research_report(task_id),
            "max_selected_papers": MAX_RESEARCH_SELECTED_PAPERS,
        },
    )


@app.post("/research-tasks/{task_id}/confirm")
def confirm_research_task(task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    try:
        enqueue_research_search_job(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.post("/research-tasks/{task_id}/cancel")
def cancel_research_task_route(task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    thread_id = task.get("checkpoint_thread_id") or ""
    if thread_id:
        checkpoint = research_checkpoint_info(thread_id)
        next_nodes = set(checkpoint["next_nodes"])
        if "request_plan_confirmation" in next_nodes:
            resume_plan_confirmation(thread_id, approved=False, task_id=task_id)
        elif "request_paper_selection" in next_nodes:
            resume_paper_selection(thread_id, cancelled=True, task_id=task_id)
        else:
            delete_research_checkpoint(thread_id)
    if not cancel_research_task(task_id):
        raise HTTPException(status_code=409, detail="Research task cannot be cancelled")
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.post("/research-tasks/{task_id}/select")
def select_research_papers(
    task_id: int,
    paper_ids: list[int] = Form(default=[]),
):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    try:
        selected_ids = validate_research_task_paper_selection(
            task,
            paper_ids,
            max_selected=MAX_RESEARCH_SELECTED_PAPERS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    thread_id = task.get("checkpoint_thread_id") or ""
    if thread_id:
        result = resume_paper_selection(
            thread_id,
            paper_ids=selected_ids,
            task_id=task_id,
        )
        if result.workflow_status != "papers_selected":
            raise HTTPException(status_code=409, detail="Research checkpoint did not resume")
    selected = select_research_task_papers(
        task_id,
        selected_ids,
        max_selected=MAX_RESEARCH_SELECTED_PAPERS,
    )
    if not selected:
        raise HTTPException(status_code=409, detail="Research papers cannot be selected")
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.post("/research-tasks/{task_id}/report")
def create_research_report_route(task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    try:
        enqueue_research_report_job(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.get("/api/research-tasks/{task_id}")
def research_task_status(task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    checkpoint = None
    if task.get("checkpoint_thread_id"):
        checkpoint = research_checkpoint_info(task["checkpoint_thread_id"])
    report = get_research_report(task_id)
    return {
        "id": task["id"],
        "status": task["status"],
        "result_count": task["result_count"],
        "selected_count": task["selected_count"],
        "error": task["error"],
        "search_job_status": task.get("search_job_status"),
        "search_job_attempts": task.get("search_job_attempts"),
        "search_job_max_attempts": task.get("search_job_max_attempts"),
        "updated_at": task["updated_at"],
        "checkpoint": checkpoint,
        "tool_calls": list_research_tool_calls(task_id),
        "report": (
            {
                "status": report["status"],
                "model": report["model"],
                "error": report["error"],
                "job_status": report.get("job_status"),
                "job_attempts": report.get("job_attempts"),
                "job_max_attempts": report.get("job_max_attempts"),
                "checkpoint_progress": report["details"].get(
                    "checkpoint_progress", {}
                ),
                "citation_validation": report["details"].get(
                    "citation_validation", {}
                ),
                "context_stats": report["details"].get("context_stats", {}),
                "context_quality": report["details"].get(
                    "context_quality", {}
                ),
                "updated_at": report["updated_at"],
            }
            if report is not None
            else None
        ),
    }


@app.get("/api/research-tasks/{task_id}/traces")
def research_task_traces(task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    return {
        "task_id": task_id,
        "thread_id": task.get("checkpoint_thread_id") or "",
        "traces": list_agent_traces(task_id=task_id),
    }


@app.get("/api/agent/metrics")
def agent_metrics():
    return build_agent_metrics()


@app.get("/papers")
def papers(request: Request):
    return templates.TemplateResponse(
        request,
        "papers.html",
        {"papers": [paper_view(paper) for paper in list_papers()]},
    )


@app.post("/papers/clear")
def clear_history():
    clear_paper_history()
    return RedirectResponse(url="/papers", status_code=303)


@app.get("/api/papers/statuses")
def paper_statuses():
    papers = list_paper_statuses()
    for paper in papers:
        paper["preparation_status"] = paper_preparation_status(paper, paper)
    return {"papers": papers}


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
            "preparation_status": get_paper_preparation_status(paper_id),
        },
    )


@app.post("/papers/{paper_id}/prepare")
def prepare_paper(paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper["local_path"] and not can_attempt_download(
        paper["source"],
        paper["pdf_url"],
    ):
        raise HTTPException(status_code=409, detail="Paper has no downloadable PDF")
    try:
        enqueue_paper_preparation_job(paper_id)
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/parse")
def parse_paper(paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not paper["local_path"]:
        raise HTTPException(status_code=409, detail="Paper PDF is not downloaded")
    try:
        enqueue_parse_job(paper_id)
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/index")
def index_paper(paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    document = get_document(paper_id)
    if document is None or document["parse_status"] != PARSE_PARSED:
        raise HTTPException(status_code=409, detail="Paper is not parsed")
    try:
        enqueue_index_job(paper_id)
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/ask")
def ask_paper(
    paper_id: int,
    question: str = Form(...),
    use_reranker: bool = Form(False),
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
    reranker = (
        RERANKER_DEEPSEEK_LISTWISE if use_reranker else RERANKER_NONE
    )
    try:
        enqueue_rag_answer_job(paper_id, question, reranker)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/retry")
def retry_download(paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not can_attempt_download(paper["source"], paper["pdf_url"]):
        raise HTTPException(status_code=409, detail="Paper has no downloadable PDF")
    enqueue_download_job(paper_id, (STATUS_FAILED,))
    return RedirectResponse(url=f"/papers/{paper_id}", status_code=303)


@app.post("/papers/{paper_id}/download")
def start_download(paper_id: int):
    paper = get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not can_attempt_download(paper["source"], paper["pdf_url"]):
        raise HTTPException(status_code=409, detail="Paper has no downloadable PDF")
    enqueue_download_job(paper_id, (STATUS_AVAILABLE,))
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
    chunk_strategy: str = Form("length_boundary"),
    query_mode: str = Form("original"),
    keyword_retriever: str = Form("tfidf"),
    reranker: str = Form("none"),
    chunk_size: int = Form(1200),
    chunk_overlap: int = Form(150),
    top_k: int = Form(5),
    semantic_weight: float = Form(0.75),
):
    try:
        run_id, config = create_experiment_run(
            name=experiment_name,
            chunk_strategy=chunk_strategy,
            query_mode=query_mode,
            keyword_retriever=keyword_retriever,
            reranker=reranker,
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


@app.get("/generation-evaluation")
def generation_evaluation_dashboard(request: Request):
    generation_runs = list_generation_evaluation_runs()
    selected_run = generation_runs[0] if generation_runs else None
    return templates.TemplateResponse(
        request,
        "generation_evaluation.html",
        {
            "retrieval_runs": [
                run for run in list_evaluation_runs(100)
                if run["status"] == "completed"
            ],
            "runs": generation_runs,
            "selected_run": selected_run,
            "results": (
                list_generation_evaluation_results(selected_run["id"])
                if selected_run and selected_run["status"] == "completed"
                else []
            ),
        },
    )


@app.post("/generation-evaluation/run")
def start_generation_evaluation(
    background_tasks: BackgroundTasks,
    retrieval_run_id: int = Form(...),
    experiment_name: str = Form("RAG 生成质量评测"),
):
    try:
        run_id = create_generation_run(retrieval_run_id, experiment_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(
        run_generation_evaluation,
        run_id,
        retrieval_run_id,
    )
    return RedirectResponse(
        url=f"/generation-evaluation/runs/{run_id}",
        status_code=303,
    )


@app.get("/generation-evaluation/runs/{run_id}")
def generation_evaluation_run_detail(request: Request, run_id: int):
    selected_run = get_generation_evaluation_run(run_id)
    if selected_run is None:
        raise HTTPException(status_code=404, detail="Generation run not found")
    return templates.TemplateResponse(
        request,
        "generation_evaluation.html",
        {
            "retrieval_runs": [
                run for run in list_evaluation_runs(100)
                if run["status"] == "completed"
            ],
            "runs": list_generation_evaluation_runs(),
            "selected_run": selected_run,
            "results": (
                list_generation_evaluation_results(run_id)
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

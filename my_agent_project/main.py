import os

os.environ["PYTHONIOENCODING"] = "utf-8"

import json
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .model_service import is_deepseek_configured
from .mineru_service import is_mineru_configured
from .agent_metrics_service import build_agent_metrics
from .config import BASE_DIR, MAX_RESULTS, configure_utf8_stdio, ensure_runtime_dirs
from .db import (
    attach_agent_traces_to_task,
    cancel_research_task,
    clear_paper_history,
    create_research_followup_messages,
    create_research_task,
    enqueue_research_followup_job,
    enqueue_download_job,
    enqueue_index_job,
    enqueue_parse_job,
    enqueue_paper_preparation_job,
    enqueue_paper_summary_job,
    enqueue_rag_answer_job,
    enqueue_research_report_job,
    enqueue_research_search_job,
    export_papers_csv,
    export_papers_json,
    get_document,
    get_evaluation_run,
    get_generation_evaluation_run,
    get_paper,
    get_paper_summary,
    get_research_task,
    get_research_report,
    initialize_business_database,
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
    list_research_messages,
    list_research_tool_calls,
    list_rag_queries,
    select_research_task_papers,
    set_research_report_question,
    revise_research_task_plan,
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
from .paper_search_mcp_service import (
    DEFAULT_SOURCE_IDS,
    SEARCH_SOURCE_OPTIONS,
    normalize_source_ids,
)
from .rag_service import (
    RERANKER_DEEPSEEK_LISTWISE,
    RERANKER_NONE,
    build_access_notice,
)
from .research_agent import (
    create_research_plan,
)
from .research_conversation_service import stream_research_followup
from .research_visual_service import (
    get_visual_asset,
    visual_payloads_from_evidence,
)
from .research_checkpoint import (
    delete_research_checkpoint,
    research_checkpoint_info,
    revise_checkpointed_research_plan,
    resume_paper_selection,
    resume_plan_confirmation,
    start_checkpointed_research,
    update_checkpoint_report_question,
)
from .schemas import (
    INDEX_INDEXED,
    MAX_RESEARCH_SELECTED_PAPERS,
    PARSE_PARSED,
    RESEARCH_TASK_AWAITING,
    STATUS_AVAILABLE,
    STATUS_FAILED,
)
configure_utf8_stdio()
ensure_runtime_dirs()
initialize_business_database()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Paper Hunter", version="0.18.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

SOURCE_GROUPS = (
    {
        "id": "general",
        "label": "综合检索",
        "description": "跨学科搜索、引用信息与出版物元数据",
        "sources": (
            ("google_scholar", "跨学科网页学术搜索"),
            ("semantic", "AI 辅助的跨学科文献索引"),
            ("openalex", "开放的全球学术知识图谱"),
            ("crossref", "DOI 与出版物元数据"),
        ),
    },
    {
        "id": "medicine",
        "label": "医学与生命科学",
        "description": "医学索引、生命科学论文与开放全文",
        "sources": (
            ("pubmed", "医学与生命科学文献索引"),
            ("pmc", "生物医学免费全文库"),
            ("europepmc", "生命科学文献与开放全文"),
            ("biorxiv", "生命科学预印本"),
            ("medrxiv", "医学与健康科学预印本"),
        ),
    },
    {
        "id": "computing",
        "label": "计算机与信息安全",
        "description": "计算机会议、预印本和密码学研究",
        "sources": (
            ("arxiv", "计算机、数学等领域预印本"),
            ("dblp", "计算机论文与会议索引"),
            ("citeseerx", "计算机领域学术搜索"),
            ("iacr", "密码学与信息安全论文"),
        ),
    },
    {
        "id": "open_access",
        "label": "开放获取与研究仓库",
        "description": "补充开放论文、机构仓库、数据和软件",
        "sources": (
            ("core", "开放获取论文聚合"),
            ("openaire", "欧洲开放科研成果"),
            ("doaj", "开放获取期刊目录"),
            ("base", "机构仓库学术搜索"),
            ("zenodo", "论文、数据与软件仓库"),
            ("hal", "法国开放学术仓库"),
            ("unpaywall", "按 DOI 查找合法开放版本"),
        ),
    },
    {
        "id": "social_science",
        "label": "社会科学、商科与法律",
        "description": "社科和商业研究的工作论文与预印本",
        "sources": (("ssrn", "社科、商科与法律预印本"),),
    },
)

SOURCE_LABELS = dict(SEARCH_SOURCE_OPTIONS)
SOURCE_PICKER_GROUPS = tuple(
    {
        **group,
        "sources": tuple(
            {
                "id": source_id,
                "label": SOURCE_LABELS[source_id],
                "description": description,
            }
            for source_id, description in group["sources"]
        ),
    }
    for group in SOURCE_GROUPS
)
SOURCE_PRESETS = (
    {
        "id": "general",
        "label": "综合研究（推荐）",
        "description": "适合尚未限定学科的主题，兼顾覆盖面、引用信息和开放版本。",
        "sources": ("google_scholar", "semantic", "openalex", "crossref", "core"),
    },
    {
        "id": "computer_ai",
        "label": "计算机与人工智能",
        "description": "优先覆盖计算机会议、期刊和最新预印本。",
        "sources": ("arxiv", "google_scholar", "semantic", "openalex", "dblp"),
    },
    {
        "id": "medicine_life",
        "label": "医学与生命科学",
        "description": "PubMed 负责权威索引，PMC 和 Europe PMC 补充全文，预印本用于追踪新进展。",
        "sources": ("pubmed", "pmc", "europepmc", "medrxiv", "biorxiv", "crossref"),
    },
    {
        "id": "social_business",
        "label": "社会科学与商科",
        "description": "覆盖正式出版物，并用 SSRN 补充工作论文与早期研究。",
        "sources": ("google_scholar", "ssrn", "semantic", "openalex", "crossref"),
    },
    {
        "id": "security",
        "label": "密码学与信息安全",
        "description": "IACR ePrint 聚焦密码学，arXiv 和 dblp 补充安全与计算机研究。",
        "sources": ("iacr", "arxiv", "dblp", "semantic", "crossref"),
    },
    {
        "id": "open_access",
        "label": "开放获取优先",
        "description": "优先查找可合法开放阅读的论文、机构仓库和研究成果。",
        "sources": ("openalex", "core", "openaire", "doaj", "base", "zenodo", "hal", "unpaywall"),
    },
    {
        "id": "all",
        "label": "全部来源",
        "description": "覆盖最广，但重复结果更多，检索时间也可能更长。",
        "sources": DEFAULT_SOURCE_IDS,
    },
)
DEFAULT_UI_SOURCE_IDS = SOURCE_PRESETS[0]["sources"]

templates.env.globals.update(
    source_groups=SOURCE_PICKER_GROUPS,
    source_presets=SOURCE_PRESETS,
)


def paper_view(paper):
    item = dict(paper)
    document = get_document(int(item["id"]))
    item["preparation_status"] = paper_preparation_status(
        item,
        dict(document) if document else None,
    )
    return item


def research_messages_with_visuals(task_id: int) -> list[dict]:
    messages = list_research_messages(task_id)
    for message in messages:
        message["visuals"] = visual_payloads_from_evidence(
            message.get("evidence", [])
        )
    return messages


def enqueue_selected_paper_preparations(paper_ids: list[int]) -> list[dict]:
    """Best-effort fan-out to the existing durable per-paper preparation jobs."""
    results = []
    for paper_id in paper_ids:
        try:
            queued = enqueue_paper_preparation_job(int(paper_id))
            results.append(
                {"paper_id": int(paper_id), "result": queued, "error": ""}
            )
        except (LookupError, RuntimeError) as exc:
            # A ready, inaccessible, or already-running paper must not prevent
            # the remaining selected papers from entering their own jobs.
            results.append(
                {"paper_id": int(paper_id), "result": None, "error": str(exc)}
            )
    return results


def paper_summary_api_payload(summary: dict) -> dict:
    return {
        "id": int(summary["id"]),
        "task_id": int(summary["task_id"]),
        "paper_id": int(summary["paper_id"]),
        "status": summary["status"],
        "content": summary.get("content"),
        "model": summary.get("model"),
        "error": summary.get("error"),
        "evidence_count": len(summary.get("evidence") or []),
        "created_at": summary["created_at"],
        "updated_at": summary["updated_at"],
        "completed_at": summary.get("completed_at"),
        "job_status": summary.get("job_status"),
        "job_attempts": summary.get("job_attempts"),
        "job_max_attempts": summary.get("job_max_attempts"),
    }


def require_selected_task_paper(task_id: int, paper_id: int) -> dict:
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    if paper_id not in {int(value) for value in task["selected_paper_ids"]}:
        raise HTTPException(
            status_code=409,
            detail="Paper is not selected for this research task",
        )
    return task


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "max_results": MAX_RESULTS,
            "source_options": SEARCH_SOURCE_OPTIONS,
            "selected_sources": DEFAULT_UI_SOURCE_IDS,
        },
    )


@app.post("/search")
def search(
    request: Request,
    query: str = Form(...),
    max_results: int = Form(MAX_RESULTS),
    sources: list[str] | None = Form(default=None),
):
    query = query.strip()
    submitted_sources = list(sources or [])
    if not query:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "max_results": MAX_RESULTS,
                "error": "请输入关键词。",
                "query": query,
                "source_options": SEARCH_SOURCE_OPTIONS,
                "selected_sources": submitted_sources,
            },
            status_code=400,
        )

    max_results = max(1, min(max_results, 50))
    try:
        selected_sources = normalize_source_ids(submitted_sources)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "max_results": max_results,
                "error": str(exc),
                "query": query,
                "source_options": SEARCH_SOURCE_OPTIONS,
                "selected_sources": submitted_sources,
            },
            status_code=400,
        )
    thread_id = f"research-{uuid4()}"
    research_plan = start_checkpointed_research(
        thread_id,
        query,
        max_results,
        selected_sources,
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
        sources=selected_sources,
    )
    attach_agent_traces_to_task(thread_id, task_id)
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


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
            "messages": research_messages_with_visuals(task_id),
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


@app.post("/research-tasks/{task_id}/message")
def post_research_message(
    task_id: int,
    message: str = Form(...),
    message_kind: str = Form(default=""),
):
    message = " ".join(message.split())
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="消息不能超过2000个字符")

    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    thread_id = task.get("checkpoint_thread_id") or ""
    report = get_research_report(task_id)

    if task["status"] == "awaiting_confirmation":
        revised_question = f"{task['question']}\n补充要求：{message}"
        plan = create_research_plan(revised_question)
        revised = revise_research_task_plan(
            task_id,
            question=revised_question,
            plan_goal=plan["plan_goal"],
            plan_queries=plan["plan_queries"],
            plan_rationale=plan["plan_rationale"],
            plan_model=plan["plan_model"],
            planner_status=plan["plan_status"],
            planner_error=plan["plan_error"],
            user_message=message,
        )
        if not revised:
            raise HTTPException(status_code=409, detail="当前阶段不能修改计划")
        if thread_id:
            revise_checkpointed_research_plan(
                thread_id,
                question=revised_question,
                plan_goal=plan["plan_goal"],
                plan_queries=plan["plan_queries"],
                plan_rationale=plan["plan_rationale"],
                plan_model=plan["plan_model"],
                plan_status=plan["plan_status"],
                plan_error=plan["plan_error"],
            )
    elif task["status"] == "papers_selected" and (
        message_kind == "report_question"
        or (
            not message_kind
            and (report is None or report["status"] != "completed")
        )
    ):
        if report is not None and report["status"] in {
            "preparing",
            "retrieving",
            "generating",
        }:
            raise HTTPException(status_code=409, detail="报告正在生成，请稍后再发消息")
        if not set_research_report_question(task_id, message):
            raise HTTPException(status_code=409, detail="当前阶段不能设置报告问题")
    elif task["status"] == "papers_selected":
        try:
            enqueue_research_followup_job(task_id, message)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=409, detail="当前任务阶段暂不接受对话")

    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.post("/research-tasks/{task_id}/chat")
def stream_research_chat(
    task_id: int,
    message: str = Form(...),
):
    try:
        created = create_research_followup_messages(task_id, message)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def event_stream():
        for event, payload in stream_research_followup(
            int(created["message_id"]),
            str(created["question"]),
            current_user_message_id=int(created["user_message_id"]),
        ):
            serialized = json.dumps(payload, ensure_ascii=False)
            yield f"event: {event}\ndata: {serialized}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/research-visuals/{visual_id}")
def research_visual(visual_id: str):
    asset = get_visual_asset(visual_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Research visual not found")
    return FileResponse(
        asset.image_path,
        media_type=asset.mime_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


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
    enqueue_selected_paper_preparations(selected_ids)
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.post("/research-tasks/{task_id}/report")
def create_research_report_route(task_id: int):
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    report_question = str(task.get("report_question") or "").strip()
    if not report_question:
        raise HTTPException(status_code=409, detail="请先在对话中说明报告要回答的问题")
    thread_id = task.get("checkpoint_thread_id") or ""
    if thread_id:
        update_checkpoint_report_question(thread_id, report_question)
    try:
        enqueue_research_report_job(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"/research-tasks/{task_id}", status_code=303)


@app.post("/api/research-tasks/{task_id}/papers/{paper_id}/summary")
def create_paper_summary_route(
    task_id: int,
    paper_id: int,
    response: Response,
):
    require_selected_task_paper(task_id, paper_id)
    try:
        queued = enqueue_paper_summary_job(task_id, paper_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    summary = get_paper_summary(task_id, paper_id)
    if summary is None:
        raise HTTPException(status_code=500, detail="Paper summary was not created")
    response.status_code = 202
    return {
        "created": bool(queued["created"]),
        "job_id": int(queued["job_id"]),
        "summary": paper_summary_api_payload(summary),
    }


@app.get("/api/research-tasks/{task_id}/papers/{paper_id}/summary")
def get_paper_summary_route(task_id: int, paper_id: int):
    require_selected_task_paper(task_id, paper_id)
    summary = get_paper_summary(task_id, paper_id)
    return {
        "task_id": task_id,
        "paper_id": paper_id,
        "summary": (
            paper_summary_api_payload(summary) if summary is not None else None
        ),
    }


@app.get("/api/research-tasks/{task_id}")
def research_task_status(task_id: int, response: Response):
    response.headers["Cache-Control"] = "no-store"
    task = get_research_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    checkpoint = None
    if task.get("checkpoint_thread_id"):
        checkpoint = research_checkpoint_info(task["checkpoint_thread_id"])
    report = get_research_report(task_id)
    messages = list_research_messages(task_id)
    latest_message = messages[-1] if messages else None
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
        "latest_message_status": (
            latest_message["status"] if latest_message is not None else None
        ),
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
            "mineru_ready": is_mineru_configured(),
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

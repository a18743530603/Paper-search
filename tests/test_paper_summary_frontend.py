from pathlib import Path

from jinja2 import Environment, FileSystemLoader


PROJECT_ROOT = Path(__file__).parents[1]
TEMPLATE_DIR = PROJECT_ROOT / "my_agent_project" / "templates"


def _paper(paper_id: int, title: str, preparation_status: str) -> dict:
    return {
        "id": paper_id,
        "title": title,
        "authors": "Researcher",
        "source": "arxiv",
        "publisher": "arXiv",
        "published": "2026-08-28",
        "status": "downloaded",
        "preparation_status": preparation_status,
        "page_url": f"https://example.test/papers/{paper_id}",
        "pdf_url": f"https://example.test/papers/{paper_id}.pdf",
        "error": None,
        "downloaded_at": "2026-08-28 10:00:00",
    }


def _render_selected_papers(selected_papers: list[dict]) -> str:
    environment = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    environment.globals["url_for"] = (
        lambda name, path: f"/static{path}" if name == "static" else "/"
    )
    template = environment.get_template("research_task.html")
    task = {
        "id": 91,
        "question": "Compare selected papers",
        "status": "papers_selected",
        "plan_goal": "Read selected papers",
        "plan_queries": ["selected papers"],
        "plan_rationale": "",
        "planner_status": "planned",
        "planner_error": "",
        "plan_model": "deepseek-test",
        "selected_count": len(selected_papers),
    }
    return template.render(
        task=task,
        papers=[],
        selected_papers=selected_papers,
        tool_calls=[],
        messages=[],
        report=None,
        max_selected_papers=5,
    )


def test_each_ready_selected_paper_has_its_own_summary_endpoint():
    html = _render_selected_papers(
        [_paper(101, "Paper A", "ready"), _paper(202, "Paper B", "ready")]
    )

    assert html.count("/api/research-tasks/91/papers/101/summary") == 2
    assert html.count("/api/research-tasks/91/papers/202/summary") == 2
    assert html.count("生成论文解读") == 2
    assert "Paper A · 论文解读" in html
    assert "Paper B · 论文解读" in html


def test_unready_selected_paper_shows_disabled_preparation_state():
    html = _render_selected_papers(
        [_paper(101, "Paper A", "ready"), _paper(202, "Paper B", "indexing")]
    )

    assert 'data-paper-ready="true"' in html
    assert 'data-paper-ready="false"' in html
    assert "论文解读：准备中" in html
    assert '<button class="link-button" type="button" disabled>生成论文解读</button>' in html


def test_summary_is_optional_and_legacy_report_controls_are_hidden():
    html = _render_selected_papers([_paper(101, "Paper A", "ready")])

    assert 'data-followup-chat' in html
    assert 'data-stream-url="/research-tasks/91/chat"' in html
    assert "跨论文研究报告" not in html
    assert "设置跨论文报告问题" not in html
    assert "确认生成研究报告" not in html
    assert "/static/paper_summary.js" in html


def test_summary_script_covers_polling_states_retry_and_safe_inline_content():
    script = (
        PROJECT_ROOT / "my_agent_project" / "static" / "paper_summary.js"
    ).read_text(encoding="utf-8")

    assert 'method: "POST"' in script
    assert 'cache: "no-store"' in script
    assert '"queued", "generating"' in script
    assert '"queued", "running", "retry_wait"' in script
    assert "论文解读生成中..." in script
    assert "论文解读：已完成" in script
    assert "论文解读：生成失败" in script
    assert "control.dataset.submitting" in script
    assert 'content.textContent = summary.content || ""' in script
    assert "innerHTML" not in script

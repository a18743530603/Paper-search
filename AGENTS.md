# Paper Hunter Project Memory

This file records the important context from the conversation that created this project. It is intended for future Codex sessions opened in this repository.

## User Goal

The user wanted a resume-ready project that can:

- Accept user-provided keywords.
- Search the web for related academic papers.
- Record paper website addresses and metadata.
- Download papers locally when they are clearly free/open-access.
- Save downloaded files into a local folder.
- Be understandable to beginners and suitable for GitHub/resume presentation.

The final project is named **Paper Hunter**.

GitHub repository:

```text
https://github.com/a18743530603/Paper-search.git
```

## Product Decisions Made

- Build as a **FastAPI Web app**, not just a command-line script.
- Search sources for v1:
  - arXiv
  - Crossref
- Download policy:
  - arXiv is the primary automatic PDF download source because its PDF URL format is stable.
  - Crossref is conservative: only try downloading when metadata exposes an absolute URL ending in `.pdf`.
  - Otherwise mark the paper as `link_only` and store DOI/page URL.
- Use FastAPI `BackgroundTasks` for PDF downloads so `POST /search` can return results quickly.
- Use SQLite for local metadata storage.
- Keep Agent/LLM enhancement optional. The current `agent_service.py` is a safe extension hook and does not require an API key.
- Do not use Sci-Hub, paid databases, or login-only sources.

## Current Architecture

Main app package:

```text
my_agent_project/
```

Important modules:

- `main.py`
  - FastAPI entrypoint.
  - Defines routes:
    - `GET /`
    - `POST /search`
    - `GET /papers`
    - `GET /papers/{paper_id}`
    - `POST /papers/{paper_id}/retry`
    - `GET /export.csv`
    - `GET /export.json`
  - Connects search, database, and background download services.
- `config.py`
  - Runtime paths and environment setup.
  - Creates `downloads/`, `downloads/papers/`, and `downloads/exports/`.
  - Forces UTF-8 stdout/stderr for Windows compatibility.
- `schemas.py`
  - Defines `PaperCandidate`.
  - Defines status constants:
    - `downloading`
    - `downloaded`
    - `link_only`
    - `failed`
- `search_service.py`
  - Searches arXiv and Crossref.
  - Parses arXiv XML and Crossref JSON.
  - Generates arXiv PDF URLs.
  - Applies Crossref `.pdf` direct-link rule.
  - Turns source failures into `failed` records instead of crashing the whole search.
- `download_service.py`
  - Downloads PDFs.
  - Sanitizes filenames with invalid Windows/Linux characters replaced by `_`.
  - Avoids filename collisions.
  - Updates each paper status independently.
  - Catches single-paper download failures.
- `db.py`
  - Initializes and queries SQLite.
  - Inserts search results.
  - Updates download status.
  - Exports CSV and JSON.
- `agent_service.py`
  - Optional future Agent/LLM query enhancement hook.
  - Must remain non-blocking and optional.
- `templates/`
  - Jinja2 templates for homepage, results, history, detail page, and shared table.
- `static/styles.css`
  - UI styling.
- `tests/test_services.py`
  - Offline tests for parsing, download policy, filename sanitization, and failure fallback.

## Data Flow

```text
Browser submits keyword
  -> POST /search
  -> main.py receives form
  -> agent_service.enhance_query() optionally adjusts query
  -> search_service.search_all() searches arXiv and Crossref
  -> db.insert_papers() stores metadata
  -> main.py schedules download_service.download_paper() via BackgroundTasks
  -> results page returns immediately
  -> background tasks update SQLite status
  -> user checks GET /papers for progress
```

## Runtime Data

Runtime files are intentionally ignored by Git:

```text
downloads/
downloads/metadata.db
downloads/papers/
downloads/exports/
.venv/
.pytest_cache/
__pycache__/
```

Do not commit downloaded PDFs, SQLite databases, virtual environments, or caches.

## Commands

Run the app:

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

Open:

```text
http://127.0.0.1:8001/
```

Run tests:

```powershell
uv run pytest
```

Push future changes:

```powershell
git add .
git commit -m "Your commit message"
git push
```

## Git History Created During This Conversation

- Initialized Git repository.
- Added `.gitignore`.
- Created initial commit:

```text
008d774 Initial commit
```

- Added GitHub remote:

```text
origin https://github.com/a18743530603/Paper-search.git
```

- Renamed branch from `master` to `main`.
- Pushed initial project to GitHub.
- Rewrote README for beginners and pushed:

```text
b22e7e8 Rewrite README for Paper Hunter
```

## README Status

`README.md` has already been rewritten from the old smolagents documentation into a beginner-friendly project document. It explains:

- What the project does.
- How to run it.
- How to use it.
- Directory structure.
- Module responsibilities.
- How modules connect.
- Background task workflow.
- Crossref fallback strategy.
- Runtime data paths.
- Common commands.
- Resume description.
- Possible future extensions.

## Important User Preferences

- The user wants explanations that are beginner-friendly.
- The user wants the project to look good on a resume.
- The user values clear module explanations and practical project documentation.
- The user asked for this memory file so future conversations do not lose context.
- If modifying project memory again, keep it in the repository root as `AGENTS.md`.

## Known Environment Notes

- Workspace path:

```text
C:\Users\28319\Desktop\<Chinese folder name>\smolagent
```

- Windows path contains Chinese characters.
- Earlier `.venv` invocation had encoding/path issues; using `uv run ...` is the recommended way to run commands.
- Git safe.directory was configured during the session because the repository was initialized under a sandbox user and later operated by the Windows user.

## Current Quality Checks

The latest tested result before this memory file was added:

```text
uv run pytest
6 passed
```

No need to retest after editing only documentation unless code is changed.

# Paper Hunter

Paper Hunter 是一个用于简历展示的学术文献检索与开放获取下载助手。用户在网页中输入关键词后，应用会检索 arXiv 和 Crossref，保存论文元数据、来源链接和下载状态，并把明确可免费下载的 PDF 放入后台任务处理。

## 功能

- FastAPI Web 界面：搜索、结果页、历史页、详情页、CSV/JSON 导出。
- arXiv 自动下载：根据 arXiv 规范链接在后台下载 PDF。
- Crossref 平滑降级：没有 `.pdf` 绝对直链时只保存 DOI/网页链接，状态为 `link_only`。
- SQLite 元数据归档：保存标题、作者、摘要、来源、URL、本地路径和错误原因。
- Windows 友好：入口强制 UTF-8 标准输出，文件名写入前会清洗非法字符。
- 可选 Agent 增强入口：无 API Key 时正常运行，有 API Key 时可扩展关键词优化和摘要能力。

## 运行

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

然后打开：

```text
http://127.0.0.1:8001/
```

## 测试

```powershell
uv run pytest
```

## 简历写法

基于 FastAPI 设计并实现学术文献检索与开放获取下载系统，聚合 arXiv 与 Crossref API，使用 BackgroundTasks 将 PDF 下载异步化，结合 SQLite 记录论文元数据、URL、本地文件路径与下载状态。针对 Crossref 链接不稳定问题设计保守降级策略，并实现文件名安全清洗、Windows UTF-8 编码兼容和单篇下载失败隔离，提高系统稳定性与可演示性。

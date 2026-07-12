# Paper Hunter 的 Origin 绘图功能

Paper Hunter 可以把论文统计结果整理成适合 Origin 导入的数据。如果同一台 Windows 电脑已经安装 Origin 或 OriginPro，项目还可以调用 Origin 自动创建图表。

## Codex 如何操作 Origin

项目不会通过模拟鼠标点击来操作 Origin，而是使用 OriginLab 提供的 Python 自动化包 `originpro`。

工作流程如下：

```text
SQLite 论文记录
  -> my_agent_project.origin_service
  -> downloads/exports/origin/paper_hunter_origin_summary.csv
  -> originpro 启动 Origin
  -> 创建 Origin 工作簿和工作表
  -> 生成柱状图
  -> 保存 PNG 图片和 .opju 项目文件
```

## 当前生成的统计图

- 按来源统计论文数量。
- 按下载状态统计论文数量。
- 统计论文数量最多的出版商。
- 按发表年份统计论文数量。

## 环境要求

只导出 CSV 不需要安装 Origin。

自动绘图需要满足以下条件：

- 使用 Windows。
- 已安装 Origin 或 OriginPro。
- 当前项目的 Python 环境已安装 `originpro`。

安装可选依赖：

```powershell
uv pip install originpro
```

## 从网页使用

启动应用：

```powershell
uv run uvicorn my_agent_project.main:app --host 127.0.0.1 --port 8001
```

打开：

```text
http://127.0.0.1:8001/origin
```

如果 Origin 自动化成功，页面会显示生成的图表图片和保存后的 Origin 项目路径。

如果本机没有可用的 Origin，页面仍会生成：

```text
downloads/exports/origin/paper_hunter_origin_summary.csv
```

可以手动把该 CSV 文件导入 Origin。

## 从命令行使用

```powershell
uv run python -m my_agent_project.origin_service
```

该命令会先生成 CSV，然后尝试启动 Origin 并创建图表。

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .config import ORIGIN_EXPORTS_DIR, ensure_runtime_dirs
from .db import list_papers


ChartRows = list[tuple[str, int]]


@dataclass
class OriginOutput:
    summary_csv: Path
    image_paths: list[Path]
    project_path: Path | None
    origin_started: bool
    message: str
    error: str | None = None


class OriginAutomationError(RuntimeError):
    pass


def _text(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    cleaned = str(value).strip()
    return cleaned or fallback


def _published_year(value: object) -> str:
    text = _text(value, "Unknown")
    if len(text) >= 4 and text[:4].isdigit():
        return text[:4]
    return "Unknown"


def _top(counter: Counter[str], limit: int | None = None) -> ChartRows:
    rows = counter.most_common(limit)
    return [(label, count) for label, count in rows if count > 0]


def build_origin_chart_tables(rows: Iterable[Mapping[str, object]]) -> dict[str, ChartRows]:
    source_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    publisher_counts: Counter[str] = Counter()
    year_counts: Counter[str] = Counter()

    for row in rows:
        source_counts[_text(row["source"], "Unknown")] += 1
        status_counts[_text(row["status"], "Unknown")] += 1
        publisher_counts[_text(row["publisher"], "Unknown")] += 1
        year_counts[_published_year(row["published"])] += 1

    return {
        "source": _top(source_counts),
        "status": _top(status_counts),
        "publisher": _top(publisher_counts, limit=10),
        "year": sorted(_top(year_counts), key=lambda item: item[0]),
    }


def write_origin_summary_csv(tables: dict[str, ChartRows]) -> Path:
    ensure_runtime_dirs()
    path = ORIGIN_EXPORTS_DIR / "paper_hunter_origin_summary.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["chart", "category", "count"])
        for chart_name, rows in tables.items():
            for category, count in rows:
                writer.writerow([chart_name, category, count])
    return path


def _plot_tables_with_origin(tables: dict[str, ChartRows]) -> tuple[list[Path], Path]:
    try:
        import originpro as op
    except ImportError as exc:
        raise OriginAutomationError(
            "Python package 'originpro' is not installed. Install Origin/OriginPro first, "
            "then run: uv pip install originpro"
        ) from exc

    if not any(tables.values()):
        raise OriginAutomationError("No paper records are available to plot.")

    ensure_runtime_dirs()
    image_paths: list[Path] = []
    project_path = ORIGIN_EXPORTS_DIR / "paper_hunter_origin.opju"

    try:
        op.set_show(True)
        op.new()

        for chart_name, rows in tables.items():
            if not rows:
                continue

            categories = [category for category, _count in rows]
            counts = [count for _category, count in rows]
            worksheet = op.new_sheet("w", lname=f"{chart_name}_data")
            worksheet.from_list(0, categories, lname="Category")
            worksheet.from_list(1, counts, lname="Count")

            graph = op.new_graph(template="column")
            layer = graph[0]
            layer.add_plot(worksheet, colx=0, coly=1, type="column")
            layer.rescale()

            image_path = ORIGIN_EXPORTS_DIR / f"paper_hunter_{chart_name}.png"
            graph.save_fig(str(image_path))
            image_paths.append(image_path)

        op.save(str(project_path))
    except Exception as exc:
        raise OriginAutomationError(str(exc)) from exc

    return image_paths, project_path


def create_origin_outputs(*, launch_origin: bool = True) -> OriginOutput:
    rows = [dict(row) for row in list_papers(limit=10_000)]
    tables = build_origin_chart_tables(rows)
    summary_csv = write_origin_summary_csv(tables)

    if not rows:
        return OriginOutput(
            summary_csv=summary_csv,
            image_paths=[],
            project_path=None,
            origin_started=False,
            message="No paper records yet. Search papers first, then generate Origin charts.",
        )

    if not launch_origin:
        return OriginOutput(
            summary_csv=summary_csv,
            image_paths=[],
            project_path=None,
            origin_started=False,
            message="Origin-ready summary CSV has been generated.",
        )

    try:
        image_paths, project_path = _plot_tables_with_origin(tables)
    except OriginAutomationError as exc:
        return OriginOutput(
            summary_csv=summary_csv,
            image_paths=[],
            project_path=None,
            origin_started=False,
            message="Origin-ready summary CSV has been generated, but Origin was not started.",
            error=str(exc),
        )

    return OriginOutput(
        summary_csv=summary_csv,
        image_paths=image_paths,
        project_path=project_path,
        origin_started=True,
        message="Origin charts and project file have been generated.",
    )


def main() -> None:
    output = create_origin_outputs()
    print(output.message)
    print(f"CSV: {output.summary_csv}")
    if output.project_path:
        print(f"Origin project: {output.project_path}")
    for image_path in output.image_paths:
        print(f"Image: {image_path}")
    if output.error:
        print(f"Origin error: {output.error}")


if __name__ == "__main__":
    main()

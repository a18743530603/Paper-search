import csv
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .config import BASE_DIR, PAPERS_DIR, SEED_EMBEDDING_MODEL
from .db import (
    create_evaluation_run,
    finish_evaluation_run,
    get_document,
    list_evaluation_cases,
    register_local_paper,
    save_evaluation_result,
    upsert_evaluation_case,
)
from .pdf_service import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, parse_paper_pdf
from .rag_service import (
    KEYWORD_WEIGHT,
    SEMANTIC_WEIGHT,
    index_paper_chunks,
    retrieve_relevant_chunks,
)
from .schemas import INDEX_INDEXED, PARSE_PARSED


BENCHMARK_PATH = BASE_DIR / "benchmarks" / "chunking_baseline.csv"
BASELINE_STRATEGY = "length_boundary"
DEFAULT_TOP_K = 5
EVIDENCE_MATCH_THRESHOLD = 0.65
TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def find_local_pdf(title: str, directory: Path = PAPERS_DIR) -> Path:
    normalized_title = _normalize_title(title)
    ranked: list[tuple[float, Path]] = []
    for path in directory.glob("*.pdf"):
        normalized_stem = _normalize_title(path.stem)
        if not normalized_stem:
            continue
        if normalized_title.startswith(normalized_stem) or normalized_stem.startswith(
            normalized_title
        ):
            score = 1.0
        else:
            score = SequenceMatcher(None, normalized_title, normalized_stem).ratio()
        ranked.append((score, path))
    if not ranked:
        raise FileNotFoundError(f"downloads/papers 中没有 PDF：{title}")
    score, path = max(ranked, key=lambda item: item[0])
    if score < 0.65:
        raise FileNotFoundError(f"没有找到与论文标题匹配的 PDF：{title}")
    return path


def import_benchmark_cases(path: Path = BENCHMARK_PATH) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"评测数据不存在：{path}")
    paper_ids: dict[str, int] = {}
    imported = 0
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            title = row["paper_title"].strip()
            if title not in paper_ids:
                paper_ids[title] = register_local_paper(title, find_local_pdf(title))
            upsert_evaluation_case(
                paper_ids[title],
                row["question_type"].strip(),
                row["question"].strip(),
                row["reference_answer"].strip(),
                int(row["evidence_page"]),
                row["evidence_text"].strip(),
            )
            imported += 1
    return {"paper_count": len(paper_ids), "case_count": imported, "paper_ids": paper_ids}


def prepare_benchmark() -> dict:
    imported = import_benchmark_cases()
    prepared: list[dict] = []
    for paper_id in imported["paper_ids"].values():
        document = get_document(paper_id)
        if document is None or document["parse_status"] != PARSE_PARSED:
            parse_paper_pdf(paper_id)
            document = get_document(paper_id)
        if document is None or document["parse_status"] != PARSE_PARSED:
            error = document["error"] if document else "解析状态不存在"
            raise RuntimeError(f"论文 {paper_id} 解析失败：{error}")
        if document["index_status"] != INDEX_INDEXED:
            index_paper_chunks(paper_id)
            document = get_document(paper_id)
        if document is None or document["index_status"] != INDEX_INDEXED:
            error = document["index_error"] if document else "索引状态不存在"
            raise RuntimeError(f"论文 {paper_id} 索引失败：{error}")
        prepared.append(
            {
                "paper_id": paper_id,
                "page_count": document["page_count"],
                "chunk_count": document["chunk_count"],
                "vectorizer": document["vectorizer"],
            }
        )
    return {**imported, "prepared": prepared}


def _evidence_tokens(text: str) -> list[str]:
    normalized = re.sub(r"(?<=\w)-\s+(?=\w)", "", text.lower())
    return TOKEN_PATTERN.findall(normalized)


def evidence_coverage(evidence_text: str, candidate_text: str) -> float:
    expected = Counter(_evidence_tokens(evidence_text))
    if not expected:
        return 0.0
    actual = Counter(_evidence_tokens(candidate_text))
    matched = sum(min(count, actual[token]) for token, count in expected.items())
    return matched / sum(expected.values())


def create_baseline_run(top_k: int = DEFAULT_TOP_K) -> int:
    cases = list_evaluation_cases()
    if not cases:
        raise RuntimeError("尚未导入评测案例")
    not_ready = [
        case
        for case in cases
        if case["parse_status"] != PARSE_PARSED
        or case["index_status"] != INDEX_INDEXED
    ]
    if not_ready:
        raise RuntimeError("评测论文尚未全部完成解析和索引")
    return create_evaluation_run(
        name="固定边界分块基线",
        chunk_strategy=BASELINE_STRATEGY,
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        embedding_model=SEED_EMBEDDING_MODEL or "local-tfidf-v1",
        semantic_weight=SEMANTIC_WEIGHT,
        keyword_weight=KEYWORD_WEIGHT,
        top_k=top_k,
        case_count=len(cases),
    )


def _aggregate_metrics(results: list[dict], top_k: int) -> dict:
    count = len(results)
    if not count:
        return {}

    def hit_at(k: int, items: list[dict] = results) -> float:
        if not items:
            return 0.0
        return sum(1 for item in items if item["hit_rank"] and item["hit_rank"] <= k) / len(items)

    def metrics_for(items: list[dict]) -> dict:
        mean_reciprocal_rank = (
            sum(item["reciprocal_rank"] for item in items) / len(items)
            if items
            else 0.0
        )
        mean_best_coverage = (
            sum(item["best_coverage"] for item in items) / len(items)
            if items
            else 0.0
        )
        return {
            "case_count": len(items),
            "hit_at_1": round(hit_at(1, items), 4),
            "hit_at_3": round(hit_at(3, items), 4),
            "hit_at_5": round(hit_at(min(5, top_k), items), 4),
            "recall_at_1": round(hit_at(1, items), 4),
            "recall_at_3": round(hit_at(3, items), 4),
            "recall_at_5": round(hit_at(min(5, top_k), items), 4),
            "mrr_at_5": round(mean_reciprocal_rank, 4),
            "mean_best_coverage": round(mean_best_coverage, 4),
        }

    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for result in results:
        grouped[result["question_type"]].append(result)
    metrics = metrics_for(results)
    metrics["error_count"] = sum(1 for result in results if result["error"])
    metrics["by_question_type"] = {
        question_type: metrics_for(items)
        for question_type, items in grouped.items()
    }
    return metrics


def run_evaluation(run_id: int, top_k: int = DEFAULT_TOP_K) -> None:
    cases = list_evaluation_cases()
    results: list[dict] = []
    try:
        for case in cases:
            hit_rank = None
            best_coverage = 0.0
            error = None
            stored_retrieved: list[dict] = []
            try:
                retrieved = retrieve_relevant_chunks(
                    case["paper_id"], case["question"], top_k=top_k
                )
                for rank, chunk in enumerate(retrieved, start=1):
                    coverage = (
                        evidence_coverage(case["evidence_text"], chunk["content"])
                        if chunk["page_number"] == case["evidence_page"]
                        else 0.0
                    )
                    best_coverage = max(best_coverage, coverage)
                    matched = coverage >= EVIDENCE_MATCH_THRESHOLD
                    if matched and hit_rank is None:
                        hit_rank = rank
                    stored_retrieved.append(
                        {
                            "rank": rank,
                            "page_number": chunk["page_number"],
                            "chunk_index": chunk["chunk_index"],
                            "score": chunk["score"],
                            "semantic_score": chunk["semantic_score"],
                            "keyword_score": chunk["keyword_score"],
                            "evidence_coverage": round(coverage, 4),
                            "matched": matched,
                            "content": chunk["content"][:800],
                        }
                    )
            except Exception as exc:
                error = str(exc)[:500]
            reciprocal_rank = 1 / hit_rank if hit_rank and hit_rank <= 5 else 0.0
            save_evaluation_result(
                run_id,
                case["id"],
                hit_rank=hit_rank,
                reciprocal_rank=reciprocal_rank,
                best_coverage=best_coverage,
                retrieved=stored_retrieved,
                error=error,
            )
            results.append(
                {
                    "question_type": case["question_type"],
                    "hit_rank": hit_rank,
                    "reciprocal_rank": reciprocal_rank,
                    "best_coverage": best_coverage,
                    "error": error,
                }
            )
        finish_evaluation_run(
            run_id,
            "completed",
            metrics=_aggregate_metrics(results, top_k),
        )
    except Exception as exc:
        finish_evaluation_run(run_id, "failed", error=str(exc)[:500])

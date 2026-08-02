import csv
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .config import BASE_DIR, PAPERS_DIR, SEED_EMBEDDING_MODEL
from .db import (
    create_evaluation_run,
    finish_evaluation_run,
    get_document,
    list_evaluation_cases,
    list_paper_chunks,
    register_local_paper,
    save_evaluation_result,
    upsert_evaluation_case,
)
from .pdf_service import (
    CHUNK_STRATEGY_LENGTH,
    CHUNK_STRATEGY_PARENT_CHILD,
    CHUNK_STRATEGY_SEMANTIC,
    CHUNK_STRATEGY_SEMANTIC_LOCAL,
    CHUNK_STRATEGY_TABLE_CONTEXT,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    SUPPORTED_CHUNK_STRATEGIES,
    SEMANTIC_BREAKPOINT_PERCENTILE,
    SEMANTIC_UNIT_SIZE,
    TABLE_BODY_PREFIX,
    TABLE_CONTEXT_PREFIX,
    parent_child_sizes,
    parse_paper_pdf,
)
from .rag_service import (
    KEYWORD_WEIGHT,
    KEYWORD_RETRIEVER_TFIDF,
    RERANK_CANDIDATE_K,
    RERANKER_DEEPSEEK_LISTWISE,
    RERANKER_NONE,
    SEMANTIC_WEIGHT,
    SUPPORTED_KEYWORD_RETRIEVERS,
    SUPPORTED_RERANKERS,
    cache_active_chunk_embeddings,
    document_index_is_reusable,
    embed_semantic_units,
    index_paper_chunks,
    retrieve_bilingual_rrf_chunks,
    retrieve_relevant_chunks,
    rerank_candidates_with_deepseek,
)
from .schemas import INDEX_INDEXED, PARSE_PARSED


BENCHMARK_PATH = BASE_DIR / "benchmarks" / "chunking_baseline.csv"
ENGLISH_QUERY_PATH = BASE_DIR / "benchmarks" / "retrieval_queries_en.csv"
BASELINE_STRATEGY = CHUNK_STRATEGY_LENGTH
DEFAULT_TOP_K = 5
EVIDENCE_MATCH_THRESHOLD = 0.65
TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
SECTION_PREFIX_PATTERN = re.compile(r"^\[Section: (?P<section>.+?)]")
TABLE_CONTEXT_PATTERN = re.compile(
    rf"^{re.escape(TABLE_CONTEXT_PREFIX)}\n(?P<context>.+?)\n"
    rf"{re.escape(TABLE_BODY_PREFIX)}\n",
    re.DOTALL,
)
MIN_CHUNK_SIZE = 300
MAX_CHUNK_SIZE = 3000
MAX_TOP_K = 20
QUERY_MODE_ORIGINAL = "original"
QUERY_MODE_ENGLISH = "english"
QUERY_MODE_BILINGUAL_RRF = "bilingual_rrf"
SUPPORTED_QUERY_MODES = {
    QUERY_MODE_ORIGINAL,
    QUERY_MODE_ENGLISH,
    QUERY_MODE_BILINGUAL_RRF,
}


@dataclass(frozen=True)
class ExperimentConfig:
    chunk_strategy: str
    query_mode: str
    keyword_retriever: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    semantic_weight: float
    keyword_weight: float
    reranker: str = RERANKER_NONE


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


def load_english_queries(path: Path = ENGLISH_QUERY_PATH) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"英文检索问题不存在：{path}")
    queries: dict[str, str] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        for row_number, row in enumerate(csv.DictReader(file), start=2):
            question = str(row.get("question") or "").strip()
            retrieval_query = str(row.get("retrieval_query_en") or "").strip()
            if not question or not retrieval_query:
                raise ValueError(f"英文检索问题第 {row_number} 行存在空值")
            if question in queries:
                raise ValueError(f"英文检索问题重复：{question}")
            queries[question] = retrieval_query
    if not queries:
        raise ValueError("英文检索问题为空")
    return queries


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


def validate_experiment_config(
    chunk_strategy: str,
    chunk_size: int,
    chunk_overlap: int,
    top_k: int,
    semantic_weight: float,
    query_mode: str = QUERY_MODE_ORIGINAL,
    keyword_retriever: str = KEYWORD_RETRIEVER_TFIDF,
    reranker: str = RERANKER_NONE,
) -> ExperimentConfig:
    if chunk_strategy not in SUPPORTED_CHUNK_STRATEGIES:
        raise ValueError(f"不支持的分块策略：{chunk_strategy}")
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size 必须在 {MIN_CHUNK_SIZE} 到 {MAX_CHUNK_SIZE} 之间"
        )
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须大于等于 0 且小于 chunk_size")
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k 必须在 1 到 {MAX_TOP_K} 之间")
    if not 0 <= semantic_weight <= 1:
        raise ValueError("semantic_weight 必须在 0 到 1 之间")
    if query_mode not in SUPPORTED_QUERY_MODES:
        raise ValueError(f"不支持的检索问题语言：{query_mode}")
    if keyword_retriever not in SUPPORTED_KEYWORD_RETRIEVERS:
        raise ValueError(f"不支持的关键词检索器：{keyword_retriever}")
    if reranker not in SUPPORTED_RERANKERS:
        raise ValueError(f"不支持的重排器：{reranker}")
    semantic_weight = round(semantic_weight, 2)
    return ExperimentConfig(
        chunk_strategy=chunk_strategy,
        query_mode=query_mode,
        keyword_retriever=keyword_retriever,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=top_k,
        semantic_weight=semantic_weight,
        keyword_weight=round(1 - semantic_weight, 2),
        reranker=reranker,
    )


def create_experiment_run(
    *,
    name: str = "固定边界分块实验",
    chunk_strategy: str = BASELINE_STRATEGY,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    top_k: int = DEFAULT_TOP_K,
    semantic_weight: float = SEMANTIC_WEIGHT,
    query_mode: str = QUERY_MODE_ORIGINAL,
    keyword_retriever: str = KEYWORD_RETRIEVER_TFIDF,
    reranker: str = RERANKER_NONE,
) -> tuple[int, ExperimentConfig]:
    config = validate_experiment_config(
        chunk_strategy,
        chunk_size,
        chunk_overlap,
        top_k,
        semantic_weight,
        query_mode=query_mode,
        keyword_retriever=keyword_retriever,
        reranker=reranker,
    )
    cases = list_evaluation_cases()
    if not cases:
        raise RuntimeError("尚未导入评测案例")
    run_id = create_evaluation_run(
        name=name.strip()[:80] or "固定边界分块实验",
        chunk_strategy=config.chunk_strategy,
        query_mode=config.query_mode,
        keyword_retriever=config.keyword_retriever,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        embedding_model=SEED_EMBEDDING_MODEL or "local-tfidf-v1",
        semantic_weight=config.semantic_weight,
        keyword_weight=config.keyword_weight,
        top_k=config.top_k,
        case_count=len(cases),
        reranker=config.reranker,
    )
    return run_id, config


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
    run_id, _config = create_experiment_run(
        name="固定边界分块基线",
        top_k=top_k,
    )
    return run_id


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


def run_evaluation(
    run_id: int,
    top_k: int = DEFAULT_TOP_K,
    semantic_weight: float = SEMANTIC_WEIGHT,
    keyword_weight: float = KEYWORD_WEIGHT,
    query_mode: str = QUERY_MODE_ORIGINAL,
    keyword_retriever: str = KEYWORD_RETRIEVER_TFIDF,
    reranker: str = RERANKER_NONE,
    extra_metrics: dict | None = None,
    cache_stats: dict[str, int] | None = None,
    experiment_started_at: float | None = None,
) -> None:
    evaluation_started_at = time.perf_counter()
    cases = list_evaluation_cases()
    results: list[dict] = []
    try:
        english_queries = (
            load_english_queries()
            if query_mode in {QUERY_MODE_ENGLISH, QUERY_MODE_BILINGUAL_RRF}
            else {}
        )
        missing_queries = [
            case["question"]
            for case in cases
            if query_mode in {QUERY_MODE_ENGLISH, QUERY_MODE_BILINGUAL_RRF}
            and case["question"] not in english_queries
        ]
        if missing_queries:
            raise RuntimeError(
                f"缺少 {len(missing_queries)} 条固定英文检索问题"
            )
        for case in cases:
            hit_rank = None
            best_coverage = 0.0
            best_coverage_at_5 = 0.0
            error = None
            stored_retrieved: list[dict] = []
            try:
                retrieval_top_k = (
                    max(top_k, RERANK_CANDIDATE_K)
                    if reranker == RERANKER_DEEPSEEK_LISTWISE
                    else top_k
                )
                retrieval_kwargs = {
                    "top_k": retrieval_top_k,
                    "semantic_weight": semantic_weight,
                    "keyword_weight": keyword_weight,
                    "keyword_retriever": keyword_retriever,
                    "cache_stats": cache_stats,
                }
                if query_mode == QUERY_MODE_BILINGUAL_RRF:
                    retrieved = retrieve_bilingual_rrf_chunks(
                        case["paper_id"],
                        case["question"],
                        english_queries[case["question"]],
                        **retrieval_kwargs,
                    )
                else:
                    retrieval_query = (
                        english_queries[case["question"]]
                        if query_mode == QUERY_MODE_ENGLISH
                        else case["question"]
                    )
                    retrieved = retrieve_relevant_chunks(
                        case["paper_id"],
                        retrieval_query,
                        **retrieval_kwargs,
                    )
                if reranker == RERANKER_DEEPSEEK_LISTWISE:
                    rerank_query = (
                        english_queries[case["question"]]
                        if query_mode in {
                            QUERY_MODE_ENGLISH,
                            QUERY_MODE_BILINGUAL_RRF,
                        }
                        else case["question"]
                    )
                    retrieved = rerank_candidates_with_deepseek(
                        rerank_query,
                        retrieved,
                        top_k=top_k,
                        cache_stats=cache_stats,
                    )
                for rank, chunk in enumerate(retrieved, start=1):
                    coverage = (
                        evidence_coverage(case["evidence_text"], chunk["content"])
                        if chunk["page_number"] == case["evidence_page"]
                        else 0.0
                    )
                    best_coverage = max(best_coverage, coverage)
                    if rank <= 5:
                        best_coverage_at_5 = max(best_coverage_at_5, coverage)
                    matched = coverage >= EVIDENCE_MATCH_THRESHOLD
                    if matched and hit_rank is None:
                        hit_rank = rank
                    stored_retrieved.append(
                        {
                            "rank": rank,
                            "page_number": chunk["page_number"],
                            "chunk_index": chunk["chunk_index"],
                            "child_chunk_index": chunk.get("child_chunk_index"),
                            "score": chunk["score"],
                            "semantic_score": chunk["semantic_score"],
                            "keyword_score": chunk["keyword_score"],
                            "fusion_ranks": chunk.get("fusion_ranks", {}),
                            "retrieval_rank": chunk.get("retrieval_rank"),
                            "rerank_rank": chunk.get("rerank_rank"),
                            "evidence_coverage": round(coverage, 4),
                            "matched": matched,
                            "content": chunk["content"],
                            "retrieval_content": chunk.get(
                                "retrieval_content", ""
                            ),
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
                    "best_coverage": best_coverage_at_5,
                    "error": error,
                }
            )
        metrics = _aggregate_metrics(results, top_k)
        metrics.update(extra_metrics or {})
        metrics["cache_stats"] = dict(cache_stats or {})
        metrics["evaluation_seconds"] = round(
            time.perf_counter() - evaluation_started_at,
            3,
        )
        if experiment_started_at is not None:
            metrics["total_duration_seconds"] = round(
                time.perf_counter() - experiment_started_at,
                3,
            )
        finish_evaluation_run(
            run_id,
            "completed",
            metrics=metrics,
        )
    except Exception as exc:
        finish_evaluation_run(run_id, "failed", error=str(exc)[:500])


def run_configured_experiment(run_id: int, config: ExperimentConfig) -> None:
    experiment_started_at = time.perf_counter()
    cases = list_evaluation_cases()
    paper_ids = sorted({int(case["paper_id"]) for case in cases})
    total_chunks = 0
    total_parent_chunks = 0
    detected_sections: set[str] = set()
    detected_tables: set[str] = set()
    table_context_chunk_count = 0
    semantic_unit_count = 0
    semantic_break_count = 0
    semantic_thresholds: list[float] = []
    cache_stats: dict[str, int] = {}
    strategy_metrics: dict[str, object] = {}
    if config.chunk_strategy == CHUNK_STRATEGY_PARENT_CHILD:
        child_size, child_overlap = parent_child_sizes(
            config.chunk_size,
            config.chunk_overlap,
        )
        strategy_metrics = {
            "child_chunk_size": child_size,
            "child_chunk_overlap": child_overlap,
        }
    elif config.chunk_strategy in {
        CHUNK_STRATEGY_SEMANTIC_LOCAL,
        CHUNK_STRATEGY_SEMANTIC,
    }:
        strategy_metrics = {
            "semantic_unit_size": SEMANTIC_UNIT_SIZE,
            "semantic_breakpoint_percentile": SEMANTIC_BREAKPOINT_PERCENTILE,
            "semantic_threshold_scope": (
                "paper"
                if config.chunk_strategy == CHUNK_STRATEGY_SEMANTIC
                else "segment"
            ),
        }
    try:
        for paper_id in paper_ids:
            cache_active_chunk_embeddings(paper_id, cache_stats)
            parse_kwargs = {
                "strategy": config.chunk_strategy,
                "chunk_size": config.chunk_size,
                "overlap": config.chunk_overlap,
                "cache_stats": cache_stats,
            }
            if config.chunk_strategy in {
                CHUNK_STRATEGY_SEMANTIC_LOCAL,
                CHUNK_STRATEGY_SEMANTIC,
            }:
                parse_kwargs["semantic_embedder"] = (
                    lambda texts: embed_semantic_units(texts, cache_stats)
                )
            parse_result = parse_paper_pdf(paper_id, **parse_kwargs)
            layout_stats = (parse_result or {}).get("layout_stats", {})
            if isinstance(layout_stats, dict):
                semantic_unit_count += int(
                    layout_stats.get("semantic_unit_count", 0)
                )
                semantic_break_count += int(
                    layout_stats.get("semantic_break_count", 0)
                )
                threshold = layout_stats.get("semantic_threshold")
                if isinstance(threshold, (int, float)):
                    semantic_thresholds.append(float(threshold))
            document = get_document(paper_id)
            if document is None or document["parse_status"] != PARSE_PARSED:
                error = document["error"] if document else "解析状态不存在"
                raise RuntimeError(f"论文 {paper_id} 重新分块失败：{error}")
            total_chunks += int(document["chunk_count"] or 0)
            total_parent_chunks += int(
                (parse_result or {}).get("parent_chunk_count", 0)
            )
            for chunk in list_paper_chunks(paper_id, limit=100_000):
                match = SECTION_PREFIX_PATTERN.match(chunk["content"])
                if match:
                    detected_sections.add(match.group("section"))
                table_match = TABLE_CONTEXT_PATTERN.match(chunk["content"])
                if table_match:
                    detected_tables.add(table_match.group("context"))
                    table_context_chunk_count += 1

            if document_index_is_reusable(document):
                cache_stats["index_document_hits"] = (
                    cache_stats.get("index_document_hits", 0) + 1
                )
            else:
                cache_stats["index_document_misses"] = (
                    cache_stats.get("index_document_misses", 0) + 1
                )
                index_paper_chunks(paper_id, cache_stats)
                document = get_document(paper_id)
            if document is None or document["index_status"] != INDEX_INDEXED:
                error = document["index_error"] if document else "索引状态不存在"
                raise RuntimeError(f"论文 {paper_id} 重新索引失败：{error}")
            if not document_index_is_reusable(document):
                error = document["index_error"] or "索引与当前Embedding配置不一致"
                raise RuntimeError(
                    f"论文 {paper_id} 未完成统一的实验索引：{error}"
                )

        run_evaluation(
            run_id,
            top_k=config.top_k,
            semantic_weight=config.semantic_weight,
            keyword_weight=config.keyword_weight,
            query_mode=config.query_mode,
            keyword_retriever=config.keyword_retriever,
            reranker=config.reranker,
            extra_metrics={
                "total_chunk_count": total_chunks,
                "parent_chunk_count": total_parent_chunks,
                "detected_section_count": len(detected_sections),
                "detected_table_count": len(detected_tables),
                "table_context_chunk_count": table_context_chunk_count,
                "semantic_unit_count": semantic_unit_count,
                "semantic_break_count": semantic_break_count,
                "mean_semantic_threshold": (
                    round(sum(semantic_thresholds) / len(semantic_thresholds), 6)
                    if semantic_thresholds
                    else None
                ),
                "rerank_candidate_k": (
                    RERANK_CANDIDATE_K
                    if config.reranker == RERANKER_DEEPSEEK_LISTWISE
                    else 0
                ),
                **strategy_metrics,
                "preparation_seconds": round(
                    time.perf_counter() - experiment_started_at,
                    3,
                ),
            },
            cache_stats=cache_stats,
            experiment_started_at=experiment_started_at,
        )
    except Exception as exc:
        finish_evaluation_run(run_id, "failed", error=str(exc)[:500])

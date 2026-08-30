"""Read-only Weighted Score versus RRF comparison on frozen paper chunks.

This script never updates the Business Database. It reads frozen Ground Truth,
uses the stored sparse/dense chunk vectors and cached query vectors, and writes
only an independent JSON result file. A missing query vector is embedded through
the configured Seed endpoint without inserting it into ``embedding_cache``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_GROUND_TRUTH = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "benchmarks"
    / "retrieval_fusion_ab_v1.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "PaperHunterData"
    / "smolagent-tests"
    / "outputs"
    / "retrieval_fusion_ab_v2_positive_tfidf_results.json"
)
SEMANTIC_WEIGHT = 0.75
TFIDF_WEIGHT = 0.25
RRF_K = 60
RECALL_KS = (3, 5, 10, 20)


def _stable_rank(
    candidates: list[dict[str, Any]],
    score_key: str,
) -> dict[int, int]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item[score_key]),
            int(item["chunk_index"]),
            int(item["chunk_id"]),
        ),
    )
    return {int(item["chunk_id"]): rank for rank, item in enumerate(ordered, 1)}


def rank_fusion_candidates(
    candidates: list[dict[str, Any]],
    *,
    semantic_weight: float = SEMANTIC_WEIGHT,
    tfidf_weight: float = TFIDF_WEIGHT,
    rrf_k: int = RRF_K,
) -> list[dict[str, Any]]:
    """Return full deterministic diagnostics for weighted and RRF rankings."""
    if not candidates:
        return []
    if semantic_weight < 0 or tfidf_weight < 0:
        raise ValueError("weights must not be negative")
    weight_total = semantic_weight + tfidf_weight
    if weight_total <= 0:
        raise ValueError("at least one weight must be positive")
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")
    semantic_weight /= weight_total
    tfidf_weight /= weight_total

    for item in candidates:
        semantic = max(float(item["semantic_score"]), 0.0)
        tfidf = max(float(item["tfidf_score"]), 0.0)
        # Production weighted retrieval rounds before sorting.
        item["weighted_score"] = round(
            semantic_weight * semantic + tfidf_weight * tfidf,
            6,
        )

    semantic_ranks = _stable_rank(candidates, "semantic_score")
    # A zero TF-IDF score means that the keyword retriever did not match this
    # chunk. Do not turn zero-score ties into a document-order ranking signal.
    tfidf_candidates = [
        item for item in candidates if float(item["tfidf_score"]) > 0.0
    ]
    tfidf_ranks = _stable_rank(tfidf_candidates, "tfidf_score")
    for item in candidates:
        chunk_id = int(item["chunk_id"])
        item["semantic_rank"] = semantic_ranks[chunk_id]
        item["tfidf_rank"] = tfidf_ranks.get(chunk_id)
        item["rrf_score"] = 1 / (rrf_k + item["semantic_rank"])
        if item["tfidf_rank"] is not None:
            item["rrf_score"] += 1 / (rrf_k + item["tfidf_rank"])

    weighted_ranks = _stable_rank(candidates, "weighted_score")
    rrf_ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item["rrf_score"]),
            int(item["semantic_rank"]),
            item["tfidf_rank"] is None,
            int(item["tfidf_rank"]) if item["tfidf_rank"] is not None else 0,
            int(item["chunk_index"]),
            int(item["chunk_id"]),
        ),
    )
    rrf_ranks = {
        int(item["chunk_id"]): rank for rank, item in enumerate(rrf_ordered, 1)
    }
    for item in candidates:
        chunk_id = int(item["chunk_id"])
        item["weighted_rank"] = weighted_ranks[chunk_id]
        item["rrf_rank"] = rrf_ranks[chunk_id]
        item["semantic_score"] = round(float(item["semantic_score"]), 6)
        item["tfidf_score"] = round(float(item["tfidf_score"]), 6)
        item["rrf_score"] = round(float(item["rrf_score"]), 9)
    return candidates


def _target_metrics(rows: list[Mapping[str, Any]], rank_key: str) -> dict[str, Any]:
    ranks = [int(row[rank_key]) for row in rows]
    cases: dict[str, list[int]] = {}
    for row in rows:
        cases.setdefault(str(row["case_id"]), []).append(int(row[rank_key]))
    return {
        **{
            f"recall_at_{k}": round(
                statistics.fmean(
                    sum(rank <= k for rank in case_ranks) / len(case_ranks)
                    for case_ranks in cases.values()
                ),
                6,
            )
            for k in RECALL_KS
        },
        "mean_target_rank": round(statistics.fmean(ranks), 6),
        "median_target_rank": round(statistics.median(ranks), 6),
        "target_count": len(ranks),
        "question_count": len(cases),
    }


def _aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "weighted": _target_metrics(rows, "weighted_rank"),
        "rrf": _target_metrics(rows, "rrf_rank"),
    }


def _paper_equal_metrics(per_paper: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for algorithm in ("weighted", "rrf"):
        result[algorithm] = {}
        for metric in (
            "recall_at_3",
            "recall_at_5",
            "recall_at_10",
            "recall_at_20",
            "mean_target_rank",
            "median_target_rank",
        ):
            values = [float(item[algorithm][metric]) for item in per_paper.values()]
            result[algorithm][metric] = round(statistics.fmean(values), 6)
    return result


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_query_vector(
    conn: Any,
    namespace: str,
    question: str,
) -> tuple[list[float], str]:
    from my_agent_project.model_service import embed_with_seed

    content_hash = _content_hash(question)
    row = conn.execute(
        """
        SELECT vector_json
        FROM embedding_cache
        WHERE cache_namespace = ? AND content_hash = ?
        """,
        (namespace, content_hash),
    ).fetchone()
    if row is not None:
        return json.loads(row["vector_json"]), "embedding_cache"
    vectors, _model = embed_with_seed([question])
    if len(vectors) != 1:
        raise RuntimeError("Seed returned an unexpected query vector count")
    return vectors[0], "live_seed_without_cache_write"


def _score_paper_question(
    chunk_rows: list[Mapping[str, Any]],
    question: str,
    query_dense: list[float],
    *,
    tfidf_question: str | None = None,
) -> list[dict[str, Any]]:
    from my_agent_project.rag_service import (
        _cosine_similarity,
        _dense_cosine_similarity,
        _tfidf_vector,
        term_frequency,
    )

    sparse_vectors = [json.loads(row["vector_json"] or "{}") for row in chunk_rows]
    document_frequency: Counter[str] = Counter()
    for vector in sparse_vectors:
        document_frequency.update(vector.keys())
    question_vector = _tfidf_vector(
        term_frequency(tfidf_question if tfidf_question is not None else question),
        document_frequency,
        len(chunk_rows),
    )

    candidates: list[dict[str, Any]] = []
    for row, sparse in zip(chunk_rows, sparse_vectors):
        dense = json.loads(row["dense_vector_json"] or "[]")
        candidates.append(
            {
                "chunk_id": int(row["id"]),
                "chunk_index": int(row["chunk_index"]),
                "page": int(row["page_number"]),
                "section": str(row["section_title"] or ""),
                "content_hash": _content_hash(str(row["content"])),
                "content_summary": " ".join(str(row["content"]).split())[:240],
                "semantic_score": _dense_cosine_similarity(query_dense, dense),
                "tfidf_score": _cosine_similarity(question_vector, _tfidf_vector(
                    sparse,
                    document_frequency,
                    len(chunk_rows),
                )),
            }
        )
    return rank_fusion_candidates(candidates)


def run_experiment(
    ground_truth_path: Path,
    *,
    retrieval_queries: Mapping[str, str] | None = None,
    semantic_queries: Mapping[str, str] | None = None,
    tfidf_queries: Mapping[str, str] | None = None,
    query_vector_cache: dict[tuple[str, str], tuple[list[float], str]] | None = None,
    experiment_version: str = "retrieval-fusion-ab-v2-positive-tfidf",
) -> dict[str, Any]:
    from my_agent_project.db import connect

    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    conn = connect()
    protected_tables = (
        "papers",
        "paper_chunks",
        "research_messages",
        "research_reports",
        "paper_summaries",
    )
    table_counts_before = {
        table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        for table in protected_tables
    }
    cache_count_before = int(
        conn.execute("SELECT COUNT(*) AS count FROM embedding_cache").fetchone()["count"]
    )
    target_rows: list[dict[str, Any]] = []
    query_sources: dict[str, str] = {}
    paper_validation: list[dict[str, Any]] = []
    try:
        for paper_spec in ground_truth["papers"]:
            paper_id = int(paper_spec["paper_id"])
            paper = conn.execute(
                "SELECT id, title FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()
            document = conn.execute(
                "SELECT chunk_strategy, embedding_namespace FROM paper_documents WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            chunks = conn.execute(
                """
                SELECT id, page_number, chunk_index, section_title, content,
                       vector_json, dense_vector_json, parent_chunk_id
                FROM paper_chunks
                WHERE paper_id = ? AND vector_json IS NOT NULL
                ORDER BY chunk_index, id
                """,
                (paper_id,),
            ).fetchall()
            if paper is None or document is None:
                raise RuntimeError(f"paper {paper_id} is missing")
            if str(paper["title"]) != paper_spec["paper_title"]:
                raise RuntimeError(f"paper {paper_id} title changed")
            if len(chunks) != int(paper_spec["expected_chunk_count"]):
                raise RuntimeError(f"paper {paper_id} chunk count changed: {len(chunks)}")
            if any(chunk["parent_chunk_id"] is not None for chunk in chunks):
                raise RuntimeError("this experiment requires the frozen flat MinerU chunks")
            namespace = str(document["embedding_namespace"] or "")
            paper_validation.append(
                {
                    "paper_id": paper_id,
                    "paper_title": str(paper["title"]),
                    "chunk_count": len(chunks),
                    "chunk_strategy": str(document["chunk_strategy"]),
                    "embedding_namespace": namespace,
                }
            )
            by_id = {int(chunk["id"]): chunk for chunk in chunks}
            for case in paper_spec["cases"]:
                question = str(case["question"])
                retrieval_query = (
                    str(retrieval_queries[case["case_id"]])
                    if retrieval_queries is not None
                    else question
                )
                semantic_query = (
                    str(semantic_queries[case["case_id"]])
                    if semantic_queries is not None
                    else retrieval_query
                )
                tfidf_query = (
                    str(tfidf_queries[case["case_id"]])
                    if tfidf_queries is not None
                    else retrieval_query
                )
                question_source = str(case["question_source"])
                if question_source.startswith("research_messages:"):
                    message_id = int(question_source.split(":", 1)[1])
                    message = conn.execute(
                        "SELECT task_id, role, content FROM research_messages WHERE id = ?",
                        (message_id,),
                    ).fetchone()
                    if (
                        message is None
                        or int(message["task_id"]) != int(paper_spec["task_id"])
                        or str(message["role"]) != "user"
                        or str(message["content"]) != question
                    ):
                        raise RuntimeError(
                            f"frozen question no longer matches research_messages:{message_id}"
                        )
                vector_key = (namespace, semantic_query)
                if query_vector_cache is not None and vector_key in query_vector_cache:
                    query_dense, original_source = query_vector_cache[vector_key]
                    query_source = f"shared_experiment_vector:{original_source}"
                else:
                    query_dense, query_source = _load_query_vector(
                        conn,
                        namespace,
                        semantic_query,
                    )
                    if query_vector_cache is not None:
                        query_vector_cache[vector_key] = (query_dense, query_source)
                query_sources[case["case_id"]] = query_source
                rankings = _score_paper_question(
                    chunks,
                    semantic_query,
                    query_dense,
                    tfidf_question=tfidf_query,
                )
                ranking_by_id = {int(item["chunk_id"]): item for item in rankings}
                for target_id in case["target_chunk_ids"]:
                    target_id = int(target_id)
                    if target_id not in by_id or target_id not in ranking_by_id:
                        raise RuntimeError(
                            f"target chunk {target_id} missing for {case['case_id']}"
                        )
                    diagnostic = ranking_by_id[target_id]
                    target_rows.append(
                        {
                            "case_id": case["case_id"],
                            "question_type": case["question_type"],
                            "task_id": int(paper_spec["task_id"]),
                            "paper_id": paper_id,
                            "paper_title": paper_spec["paper_title"],
                            "question": question,
                            "retrieval_query": (
                                semantic_query if semantic_query == tfidf_query else None
                            ),
                            "semantic_query": semantic_query,
                            "tfidf_query": tfidf_query,
                            "question_source": case["question_source"],
                            "query_vector_source": query_source,
                            "target_chunk_id": target_id,
                            **{
                                key: diagnostic[key]
                                for key in (
                                    "page",
                                    "section",
                                    "content_hash",
                                    "content_summary",
                                    "semantic_score",
                                    "semantic_rank",
                                    "tfidf_score",
                                    "tfidf_rank",
                                    "weighted_score",
                                    "weighted_rank",
                                    "rrf_score",
                                    "rrf_rank",
                                )
                            },
                            "rank_delta_rrf_minus_weighted": (
                                int(diagnostic["rrf_rank"])
                                - int(diagnostic["weighted_rank"])
                            ),
                        }
                    )
        cache_count_after = int(
            conn.execute("SELECT COUNT(*) AS count FROM embedding_cache").fetchone()[
                "count"
            ]
        )
        table_counts_after = {
            table: int(
                conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                    "count"
                ]
            )
            for table in protected_tables
        }
    finally:
        conn.rollback()
        conn.close()

    per_paper = {
        paper["paper_title"]: _aggregate(
            [row for row in target_rows if row["paper_id"] == paper["paper_id"]]
        )
        for paper in paper_validation
    }
    per_type = {
        question_type: _aggregate(
            [row for row in target_rows if row["question_type"] == question_type]
        )
        for question_type in sorted({row["question_type"] for row in target_rows})
    }
    return {
        "experiment": {
            "version": experiment_version,
            "semantic_weight": SEMANTIC_WEIGHT,
            "tfidf_weight": TFIDF_WEIGHT,
            "rrf_k": RRF_K,
            "tfidf_rrf_policy": (
                "only tfidf_score > 0 receives a keyword rank; "
                "zero-score chunks receive no TF-IDF RRF contribution"
            ),
            "database_access": "read_only_by_operation; transaction_rolled_back",
            "deepseek_called": False,
            "production_ranking_modified": False,
            "user_questions_modified": False,
        },
        "paper_validation": paper_validation,
        "question_vector_sources": query_sources,
        "database_safety": {
            "embedding_cache_count_before": cache_count_before,
            "embedding_cache_count_after": cache_count_after,
            "embedding_cache_unchanged": cache_count_before == cache_count_after,
            "protected_table_counts_before": table_counts_before,
            "protected_table_counts_after": table_counts_after,
            "protected_table_counts_unchanged": (
                table_counts_before == table_counts_after
            ),
        },
        "target_rows": target_rows,
        "metrics": {
            "per_paper": per_paper,
            "all_12_questions": _aggregate(target_rows),
            "paper_equal_weight": _paper_equal_metrics(per_paper),
            "per_question_type": per_type,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_experiment(args.ground_truth)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"target_rows={len(result['target_rows'])}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

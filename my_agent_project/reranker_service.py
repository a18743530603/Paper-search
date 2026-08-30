import math
import threading
from collections.abc import Mapping, Sequence
from typing import Any


DEFAULT_RERANK_BATCH_SIZE = 20
_reranker: Any | None = None
_reranker_model_name = ""
_reranker_lock = threading.Lock()


def _create_reranker(model_name: str) -> Any:
    # Import lazily so app startup and the hybrid fallback do not depend on
    # Hugging Face availability or a completed local model download.
    from FlagEmbedding import FlagReranker

    return FlagReranker(model_name, use_fp16=False)


def _get_reranker(model_name: str) -> Any:
    global _reranker, _reranker_model_name

    normalized_model = str(model_name or "").strip()
    if not normalized_model:
        raise ValueError("reranker model name must not be empty")
    if _reranker is not None and _reranker_model_name == normalized_model:
        return _reranker
    with _reranker_lock:
        if _reranker is None or _reranker_model_name != normalized_model:
            loaded = _create_reranker(normalized_model)
            _reranker = loaded
            _reranker_model_name = normalized_model
    return _reranker


def _reset_reranker_cache() -> None:
    """Clear the process cache for isolated tests; production never calls this."""

    global _reranker, _reranker_model_name
    with _reranker_lock:
        _reranker = None
        _reranker_model_name = ""


def _normalized_scores(raw_scores: Any, expected_count: int) -> list[float]:
    if hasattr(raw_scores, "tolist"):
        raw_scores = raw_scores.tolist()
    if expected_count == 1 and isinstance(raw_scores, (int, float)):
        raw_scores = [raw_scores]
    if not isinstance(raw_scores, Sequence) or isinstance(raw_scores, (str, bytes)):
        raise ValueError("reranker returned an invalid score collection")
    if len(raw_scores) != expected_count:
        raise ValueError(
            f"reranker returned {len(raw_scores)} scores for {expected_count} candidates"
        )
    scores: list[float] = []
    for raw_score in raw_scores:
        if isinstance(raw_score, bool):
            raise ValueError("reranker returned a non-numeric score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError) as exc:
            raise ValueError("reranker returned a non-numeric score") from exc
        if not math.isfinite(score):
            raise ValueError("reranker returned a non-finite score")
        scores.append(score)
    return scores


def rerank_candidates(
    original_query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    model_name: str,
    batch_size: int = DEFAULT_RERANK_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Batch-rerank only the supplied candidate pool using original paper text."""

    query = " ".join(str(original_query or "").split())
    if not query:
        raise ValueError("original_query must not be empty")
    if not candidates:
        return []
    pairs = [
        [query, str(candidate.get("content") or "")]
        for candidate in candidates
    ]
    model = _get_reranker(model_name)
    raw_scores = model.compute_score(
        pairs,
        batch_size=max(1, min(batch_size, len(pairs))),
        normalize=True,
    )
    scores = _normalized_scores(raw_scores, len(candidates))
    reranked: list[dict[str, Any]] = []
    for candidate, score in zip(candidates, scores):
        item = dict(candidate)
        item["rerank_score"] = score
        reranked.append(item)
    reranked.sort(
        key=lambda item: (-float(item["rerank_score"]), int(item["hybrid_rank"]))
    )
    for rerank_rank, candidate in enumerate(reranked, start=1):
        candidate["rerank_rank"] = rerank_rank
    return reranked

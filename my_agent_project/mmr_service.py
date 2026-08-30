import math
from collections.abc import Mapping, Sequence
from typing import Any


MMR_EMBEDDING_FIELD = "_mmr_embedding"
MMR_GUARANTEED_RERANK_COUNT = 2


class MMRSelectionError(ValueError):
    pass


def _validated_embedding(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MMRSelectionError("candidate embedding is missing")
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise MMRSelectionError("candidate embedding is invalid") from exc
    if not vector or any(not math.isfinite(item) for item in vector):
        raise MMRSelectionError("candidate embedding is invalid")
    return vector


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise MMRSelectionError("candidate embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        raise MMRSelectionError("candidate embedding has zero norm")
    cosine = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return min(1.0, max(-1.0, cosine))


def _normalized_relevance(
    candidates: Sequence[Mapping[str, Any]],
) -> list[float]:
    scores: list[float] = []
    for candidate in candidates:
        try:
            score = float(candidate["rerank_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MMRSelectionError("rerank relevance is missing or invalid") from exc
        if not math.isfinite(score):
            raise MMRSelectionError("rerank relevance is not finite")
        scores.append(score)
    minimum = min(scores)
    maximum = max(scores)
    if maximum > minimum:
        return [(score - minimum) / (maximum - minimum) for score in scores]

    order = sorted(
        range(len(candidates)),
        key=lambda index: (int(candidates[index]["rerank_rank"]), index),
    )
    if len(order) == 1:
        return [1.0]
    relevance = [0.0] * len(candidates)
    denominator = len(order) - 1
    for position, index in enumerate(order):
        relevance[index] = (denominator - position) / denominator
    return relevance


def select_mmr_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    final_top_k: int,
    lambda_value: float = 0.5,
    guaranteed_rerank_count: int = MMR_GUARANTEED_RERANK_COUNT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Guarantee top relevance, then fill remaining slots with diverse evidence."""

    if final_top_k < 1:
        raise MMRSelectionError("final_top_k must be positive")
    if guaranteed_rerank_count < 1:
        raise MMRSelectionError("guaranteed_rerank_count must be positive")
    if not 0.0 <= lambda_value <= 1.0:
        raise MMRSelectionError("MMR lambda must be between zero and one")

    annotated = [dict(candidate) for candidate in candidates]
    if not annotated:
        return [], []
    embeddings = [
        _validated_embedding(candidate.get(MMR_EMBEDDING_FIELD))
        for candidate in annotated
    ]
    dimensions = {len(vector) for vector in embeddings}
    if len(dimensions) != 1:
        raise MMRSelectionError("candidate embedding dimensions do not match")
    relevance = _normalized_relevance(annotated)
    for candidate, normalized in zip(annotated, relevance):
        candidate["mmr_relevance"] = normalized

    input_order = {index: index for index in range(len(annotated))}
    rerank_order = sorted(
        range(len(annotated)),
        key=lambda index: (
            int(annotated[index]["rerank_rank"]),
            input_order[index],
        ),
    )
    target_count = min(final_top_k, len(annotated))
    guaranteed_count = min(guaranteed_rerank_count, target_count)
    selected_indices: list[int] = []
    for index in rerank_order[:guaranteed_count]:
        max_similarity = (
            max(
                _cosine_similarity(embeddings[index], embeddings[selected])
                for selected in selected_indices
            )
            if selected_indices
            else 0.0
        )
        mmr_score = (
            lambda_value * relevance[index]
            - (1.0 - lambda_value) * max_similarity
        )
        selected_indices.append(index)
        annotated[index].update(
            {
                "mmr_score": mmr_score,
                "mmr_rank": len(selected_indices),
                "mmr_max_similarity": max_similarity,
            }
        )

    while len(selected_indices) < target_count:
        best_index: int | None = None
        best_key: tuple[float, int, int] | None = None
        best_score = 0.0
        best_similarity = 0.0
        for index, candidate in enumerate(annotated):
            if index in selected_indices:
                continue
            max_similarity = max(
                _cosine_similarity(embeddings[index], embeddings[selected])
                for selected in selected_indices
            )
            mmr_score = (
                lambda_value * relevance[index]
                - (1.0 - lambda_value) * max_similarity
            )
            key = (
                mmr_score,
                -int(candidate["rerank_rank"]),
                -input_order[index],
            )
            if best_key is None or key > best_key:
                best_index = index
                best_key = key
                best_score = mmr_score
                best_similarity = max_similarity
        if best_index is None:
            raise MMRSelectionError("MMR could not select another candidate")
        selected_indices.append(best_index)
        annotated[best_index].update(
            {
                "mmr_score": best_score,
                "mmr_rank": len(selected_indices),
                "mmr_max_similarity": best_similarity,
            }
        )

    for index, candidate in enumerate(annotated):
        if index in selected_indices:
            continue
        max_similarity = max(
            _cosine_similarity(embeddings[index], embeddings[selected])
            for selected in selected_indices
        )
        candidate.update(
            {
                "mmr_score": (
                    lambda_value * relevance[index]
                    - (1.0 - lambda_value) * max_similarity
                ),
                "mmr_rank": None,
                "mmr_max_similarity": max_similarity,
            }
        )

    selected = sorted(
        (annotated[index] for index in selected_indices),
        key=lambda candidate: int(candidate["mmr_rank"]),
    )
    return selected, annotated

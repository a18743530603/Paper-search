from typing import Any

import httpx

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_THINKING,
    DEEPSEEK_TIMEOUT,
    SEED_API_KEY,
    SEED_BASE_URL,
    SEED_EMBEDDING_API_MODE,
    SEED_EMBEDDING_MODEL,
    SEED_TIMEOUT,
)


class ModelConfigurationError(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    pass


class EmbeddingRequestError(RuntimeError):
    pass


def is_deepseek_configured() -> bool:
    return bool(DEEPSEEK_API_KEY and DEEPSEEK_MODEL)


def is_seed_configured() -> bool:
    return bool(SEED_API_KEY and SEED_EMBEDDING_MODEL)


def enhance_query(query: str) -> str:
    """Keep search deterministic; DeepSeek is reserved for evidence-based RAG."""
    return query


def _extract_answer(payload: dict[str, Any]) -> str:
    try:
        answer = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRequestError("DeepSeek 返回了无法识别的响应格式") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise ModelRequestError("DeepSeek 没有返回有效回答")
    return answer.strip()


def ask_deepseek(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    if not is_deepseek_configured():
        raise ModelConfigurationError(
            "尚未配置 DeepSeek。请在项目根目录的 .env 中设置 DEEPSEEK_API_KEY。"
        )

    body: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    if DEEPSEEK_THINKING:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"
    else:
        body["thinking"] = {"type": "disabled"}

    try:
        response = httpx.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=DEEPSEEK_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()[:500]
        raise ModelRequestError(
            f"DeepSeek 请求失败（HTTP {exc.response.status_code}）：{detail}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelRequestError(f"无法连接 DeepSeek：{exc}") from exc

    return _extract_answer(payload), DEEPSEEK_MODEL


def _seed_api_mode() -> str:
    if SEED_EMBEDDING_API_MODE in {"text", "multimodal"}:
        return SEED_EMBEDDING_API_MODE
    if "embedding-vision" in SEED_EMBEDDING_MODEL.lower():
        return "multimodal"
    return "text"


def _request_seed_embedding(path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.post(
            f"{SEED_BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {SEED_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=SEED_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()[:500]
        raise EmbeddingRequestError(
            f"Seed Embedding 请求失败（HTTP {exc.response.status_code}）：{detail}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise EmbeddingRequestError(f"无法连接 Seed Embedding：{exc}") from exc
    if not isinstance(payload, dict):
        raise EmbeddingRequestError("Seed Embedding 返回了无法识别的响应格式")
    return payload


def _validate_vector(vector: Any) -> list[float]:
    if (
        not isinstance(vector, list)
        or not vector
        or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in vector
        )
    ):
        raise EmbeddingRequestError("Seed Embedding 返回了无效向量")
    return [float(value) for value in vector]


def _extract_multimodal_vector(payload: dict[str, Any]) -> list[float]:
    try:
        data = payload["data"]
        item = data[0] if isinstance(data, list) else data
        vector = item["embedding"]
        if (
            isinstance(vector, list)
            and len(vector) == 1
            and isinstance(vector[0], list)
        ):
            vector = vector[0]
    except (KeyError, IndexError, TypeError) as exc:
        raise EmbeddingRequestError("Seed Embedding 返回了无法识别的响应格式") from exc
    return _validate_vector(vector)


def embed_with_seed(texts: list[str]) -> tuple[list[list[float]], str]:
    if not is_seed_configured():
        raise ModelConfigurationError(
            "尚未配置 Seed Embedding。请设置 SEED_API_KEY 和 SEED_EMBEDDING_MODEL。"
        )
    if not texts or any(not text.strip() for text in texts):
        raise EmbeddingRequestError("Seed Embedding 输入不能为空")

    if _seed_api_mode() == "multimodal":
        vectors = []
        model = SEED_EMBEDDING_MODEL
        for text in texts:
            payload = _request_seed_embedding(
                "/embeddings/multimodal",
                {
                    "model": SEED_EMBEDDING_MODEL,
                    "input": [{"type": "text", "text": text}],
                    "encoding_format": "float",
                },
            )
            vectors.append(_extract_multimodal_vector(payload))
            model = str(payload.get("model") or model)
        return vectors, model

    payload = _request_seed_embedding(
        "/embeddings",
        {
            "model": SEED_EMBEDDING_MODEL,
            "input": texts,
            "encoding_format": "float",
        },
    )
    try:
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [_validate_vector(item["embedding"]) for item in ordered]
    except (KeyError, TypeError) as exc:
        raise EmbeddingRequestError("Seed Embedding 返回了无法识别的响应格式") from exc
    if len(vectors) != len(texts):
        raise EmbeddingRequestError("Seed Embedding 返回的向量数量不正确")
    model = str(payload.get("model") or SEED_EMBEDDING_MODEL)
    return vectors, model

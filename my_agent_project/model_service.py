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


def embed_with_seed(texts: list[str]) -> tuple[list[list[float]], str]:
    if not is_seed_configured():
        raise ModelConfigurationError(
            "尚未配置 Seed Embedding。请设置 SEED_API_KEY 和 SEED_EMBEDDING_MODEL。"
        )
    if not texts or any(not text.strip() for text in texts):
        raise EmbeddingRequestError("Seed Embedding 输入不能为空")

    try:
        response = httpx.post(
            f"{SEED_BASE_URL}/embeddings",
            headers={
                "Authorization": f"Bearer {SEED_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": SEED_EMBEDDING_MODEL,
                "input": texts,
                "encoding_format": "float",
            },
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

    try:
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in ordered]
    except (KeyError, TypeError) as exc:
        raise EmbeddingRequestError("Seed Embedding 返回了无法识别的响应格式") from exc
    if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
        raise EmbeddingRequestError("Seed Embedding 返回的向量数量不正确")
    model = str(payload.get("model") or SEED_EMBEDDING_MODEL)
    return vectors, model

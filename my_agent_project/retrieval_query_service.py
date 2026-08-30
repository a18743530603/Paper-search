import hashlib
import json
import re
from dataclasses import asdict, dataclass

from .config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    RESEARCH_QUERY_ROUTING_ENABLED,
)
from .db import get_cached_llm_response, upsert_cached_llm_response
from .model_service import ask_deepseek_json


QUERY_REWRITE_PROMPT_VERSION = "research-query-routing-en-v1"
MAX_RETRIEVAL_QUERY_LENGTH = 500
LATIN_LETTER_PATTERN = re.compile(r"[A-Za-z]")

QUERY_REWRITE_SYSTEM_PROMPT = """
You rewrite one user question into one concise English academic retrieval query.

Rules:
- Return JSON only: {"query": "..."}.
- Do not answer the question.
- Do not add, infer, or guess paper facts, parameters, results, or conclusions.
- Preserve paper titles, model names, algorithm names, dataset names, and acronyms.
- Express the same retrieval intent in terms likely to occur in an English paper.
- Return exactly one query. Do not split intents and do not provide alternatives.
- Keep the query concise and suitable for lexical retrieval such as TF-IDF.
""".strip()


@dataclass(frozen=True)
class RetrievalQueryRoute:
    original_query: str
    lexical_query: str
    query_routing_enabled: bool
    query_rewrite_status: str
    semantic_query_type: str
    tfidf_query_type: str
    model: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalize_query(value: str) -> str:
    return " ".join(str(value or "").split())


def _validate_lexical_query(response: object) -> str:
    if not isinstance(response, dict):
        raise ValueError("Query Rewrite 返回值必须是 JSON 对象")
    query = _normalize_query(response.get("query", ""))
    if not query:
        raise ValueError("Query Rewrite 没有返回英文检索表达")
    if len(query) > MAX_RETRIEVAL_QUERY_LENGTH:
        raise ValueError("Query Rewrite 返回的检索表达过长")
    if LATIN_LETTER_PATTERN.search(query) is None:
        raise ValueError("Query Rewrite 返回的检索表达不包含英文字符")
    return query


def _cache_namespace() -> str:
    return (
        f"deepseek|{DEEPSEEK_BASE_URL}|{DEEPSEEK_MODEL}|"
        f"{QUERY_REWRITE_PROMPT_VERSION}"
    )


def _input_hash(original_query: str) -> str:
    payload = json.dumps(
        {
            "prompt_version": QUERY_REWRITE_PROMPT_VERSION,
            "target_language": "English",
            "original_query": original_query,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def route_retrieval_query(
    original_query: str,
    *,
    enabled: bool | None = None,
) -> RetrievalQueryRoute:
    """Return the lexical query without changing the user-facing question.

    Any cache, model, response, or validation failure deterministically falls
    back to the original query so retrieval cannot fail solely because the
    optional routing helper is unavailable.
    """

    original_query = _normalize_query(original_query)
    if not original_query:
        raise ValueError("检索问题不能为空")
    routing_enabled = (
        RESEARCH_QUERY_ROUTING_ENABLED if enabled is None else bool(enabled)
    )
    if not routing_enabled:
        return RetrievalQueryRoute(
            original_query=original_query,
            lexical_query=original_query,
            query_routing_enabled=False,
            query_rewrite_status="disabled",
            semantic_query_type="original_query",
            tfidf_query_type="original_query",
        )

    namespace = _cache_namespace()
    input_hash = _input_hash(original_query)
    cache_error = ""
    try:
        cached = get_cached_llm_response(namespace, input_hash)
        if cached is not None:
            lexical_query = _validate_lexical_query(cached)
            return RetrievalQueryRoute(
                original_query=original_query,
                lexical_query=lexical_query,
                query_routing_enabled=True,
                query_rewrite_status="cache_hit",
                semantic_query_type="original_query",
                tfidf_query_type="english_retrieval_query",
                model=DEEPSEEK_MODEL,
            )
    except Exception as exc:
        cache_error = f"cache read: {type(exc).__name__}: {exc}"[:500]

    try:
        response, model = ask_deepseek_json(
            QUERY_REWRITE_SYSTEM_PROMPT,
            json.dumps(
                {
                    "task": "rewrite_for_english_academic_lexical_retrieval",
                    "original_query": original_query,
                },
                ensure_ascii=False,
            ),
            max_tokens=128,
        )
        lexical_query = _validate_lexical_query(response)
        try:
            upsert_cached_llm_response(
                namespace,
                input_hash,
                {"query": lexical_query},
            )
        except Exception:
            pass
        return RetrievalQueryRoute(
            original_query=original_query,
            lexical_query=lexical_query,
            query_routing_enabled=True,
            query_rewrite_status="generated",
            semantic_query_type="original_query",
            tfidf_query_type="english_retrieval_query",
            model=model,
            error=cache_error,
        )
    except Exception as exc:
        errors = [value for value in (cache_error, f"{type(exc).__name__}: {exc}") if value]
        return RetrievalQueryRoute(
            original_query=original_query,
            lexical_query=original_query,
            query_routing_enabled=True,
            query_rewrite_status="fallback",
            semantic_query_type="original_query",
            tfidf_query_type="original_query_fallback",
            error="; ".join(errors)[:500],
        )

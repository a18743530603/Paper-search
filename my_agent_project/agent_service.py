import os

from .config import ENABLE_AGENT


def enhance_query(query: str) -> str:
    """Optional placeholder for LLM query expansion.

    The app must run without an API key, so v1 keeps this deterministic. When
    ENABLE_AGENT and an API key are present, this hook can be replaced with a
    smolagents/OpenAI-compatible implementation without touching search routes.
    """
    has_api_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY"))
    if not ENABLE_AGENT or not has_api_key:
        return query
    return query


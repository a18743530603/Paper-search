import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .db import (
    get_document,
    get_paper,
    get_rag_query,
    list_indexed_chunks,
    list_paper_chunks,
    replace_chunk_vectors,
    update_index_status,
    update_rag_query,
)
from .model_service import ask_deepseek, embed_with_seed, is_seed_configured
from .schemas import (
    INDEX_FAILED,
    INDEX_INDEXED,
    INDEX_INDEXING,
    RAG_ANSWERED,
    RAG_FAILED,
)


VECTOR_MODEL = "local-tfidf-v1"
SEMANTIC_WEIGHT = 0.75
KEYWORD_WEIGHT = 0.25
SEED_BATCH_SIZE = 64
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")

RAG_SYSTEM_PROMPT = """你是 Paper Hunter 的学术论文阅读助手。

回答规则：
1. 只能根据系统提供的论文元数据和已解析全文片段回答，不得编造论文内容、实验结果或引用。
2. 每个有事实依据的回答都要标注论文标题和页码；没有页码的元数据必须明确标注为“仅元数据”。
3. 如果系统没有取得论文全文，必须明确告诉读者当前无法读取全文。论文可能需要在出版商网站购买、通过学校或机构订阅访问，也可能存在其他合法开放版本。
4. 无法取得全文时，必须提供系统给出的论文网页链接或 DOI 链接，供读者自行确认访问方式；不得声称论文一定收费，也不得根据摘要假装已经阅读全文。
5. 如果给出的片段不足以回答问题，直接说明“当前证据不足”，并指出还需要哪部分内容。
6. 不提供绕过付费墙、登录验证或版权限制的方法。
7. 回答末尾必须列出“证据来源”，格式为论文标题和页码。
"""


def paper_access_url(paper: Mapping[str, Any]) -> str:
    page_url = str(paper.get("page_url") or "").strip()
    if page_url:
        return page_url
    doi = str(paper.get("doi") or "").strip()
    return f"https://doi.org/{doi}" if doi else ""


def build_access_notice(paper: Mapping[str, Any]) -> str:
    url = paper_access_url(paper)
    message = (
        "当前系统尚未取得这篇论文的可读取全文。论文可能需要在出版商网站购买，"
        "或通过学校、图书馆、机构订阅访问；也可能存在其他合法开放版本。"
    )
    if url:
        message += f" 请前往论文页面确认访问方式：{url}"
    return message


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def term_frequency(text: str) -> dict[str, float]:
    counts = Counter(tokenize(text))
    total = sum(counts.values())
    if not total:
        return {}
    return {term: count / total for term, count in counts.items()}


def index_paper_chunks(paper_id: int) -> None:
    update_index_status(paper_id, INDEX_INDEXING, error=None)
    try:
        chunks = list_paper_chunks(paper_id, limit=100_000)
        if not chunks:
            raise RuntimeError("论文尚未解析出文本块")
        vectors = {
            int(chunk["id"]): term_frequency(chunk["content"])
            for chunk in chunks
        }
        if not any(vectors.values()):
            raise RuntimeError("文本块没有可用于检索的词项")
        dense_vectors: dict[int, list[float]] = {}
        vectorizer = VECTOR_MODEL
        warning = None
        if is_seed_configured():
            try:
                seed_model = ""
                for start in range(0, len(chunks), SEED_BATCH_SIZE):
                    batch = chunks[start : start + SEED_BATCH_SIZE]
                    batch_vectors, seed_model = embed_with_seed(
                        [chunk["content"] for chunk in batch]
                    )
                    dense_vectors.update(
                        {
                            int(chunk["id"]): vector
                            for chunk, vector in zip(batch, batch_vectors)
                        }
                    )
                vectorizer = f"hybrid:{seed_model}+{VECTOR_MODEL}"
            except Exception as exc:
                dense_vectors = {}
                warning = f"Seed Embedding 暂时不可用，已降级为 TF-IDF：{exc}"[:500]
        replace_chunk_vectors(
            paper_id,
            vectors,
            vectorizer=vectorizer,
            dense_vectors=dense_vectors,
            warning=warning,
        )
    except Exception as exc:
        update_index_status(paper_id, INDEX_FAILED, error=str(exc)[:500])


def _tfidf_vector(
    term_frequencies: Mapping[str, float],
    document_frequency: Mapping[str, int],
    document_count: int,
) -> dict[str, float]:
    return {
        term: tf * (math.log((document_count + 1) / (document_frequency.get(term, 0) + 1)) + 1)
        for term, tf in term_frequencies.items()
    }


def _cosine_similarity(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    dot = sum(value * right.get(term, 0.0) for term, value in left.items())
    return dot / (left_norm * right_norm)


def _dense_cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def retrieve_relevant_chunks(
    paper_id: int,
    question: str,
    *,
    top_k: int = 6,
    semantic_weight: float = SEMANTIC_WEIGHT,
    keyword_weight: float = KEYWORD_WEIGHT,
) -> list[dict]:
    if semantic_weight < 0 or keyword_weight < 0:
        raise ValueError("retrieval weights must not be negative")
    total_weight = semantic_weight + keyword_weight
    if total_weight <= 0:
        raise ValueError("at least one retrieval weight must be positive")
    semantic_weight /= total_weight
    keyword_weight /= total_weight

    rows = list_indexed_chunks(paper_id)
    if not rows:
        return []

    stored_vectors: list[dict[str, float]] = []
    document_frequency: Counter[str] = Counter()
    for row in rows:
        try:
            vector = json.loads(row["vector_json"] or "{}")
        except json.JSONDecodeError:
            vector = {}
        stored_vectors.append(vector)
        document_frequency.update(vector.keys())

    question_tf = term_frequency(question)
    question_vector = _tfidf_vector(question_tf, document_frequency, len(rows))
    question_dense: list[float] | None = None
    if is_seed_configured() and any(row["dense_vector_json"] for row in rows):
        try:
            dense_vectors, _model = embed_with_seed([question])
            question_dense = dense_vectors[0]
        except Exception:
            question_dense = None

    ranked: list[dict] = []
    for row, stored_vector in zip(rows, stored_vectors):
        chunk_vector = _tfidf_vector(stored_vector, document_frequency, len(rows))
        keyword_score = _cosine_similarity(question_vector, chunk_vector)
        semantic_score: float | None = None
        if question_dense is not None and row["dense_vector_json"]:
            try:
                stored_dense = json.loads(row["dense_vector_json"])
                semantic_score = _dense_cosine_similarity(question_dense, stored_dense)
            except (json.JSONDecodeError, TypeError):
                semantic_score = None
        if semantic_score is None:
            score = keyword_score
        else:
            score = (
                semantic_weight * max(semantic_score, 0.0)
                + keyword_weight * max(keyword_score, 0.0)
            )
        if score <= 0:
            continue
        ranked.append(
            {
                "chunk_id": row["id"],
                "page_number": row["page_number"],
                "chunk_index": row["chunk_index"],
                "content": row["content"],
                "score": round(score, 6),
                "semantic_score": (
                    round(semantic_score, 6) if semantic_score is not None else None
                ),
                "keyword_score": round(keyword_score, 6),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[: max(1, top_k)]


def build_rag_prompt(
    question: str,
    paper: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
) -> str:
    title = str(paper.get("title") or "未知标题")
    url = paper_access_url(paper) or "未提供"
    if chunks:
        evidence = "\n\n".join(
            f"[论文：{title}；第 {chunk['page_number']} 页]\n{chunk['content']}"
            for chunk in chunks
        )
        access = "系统已取得并解析全文片段。"
    else:
        evidence = "没有可用的全文片段。"
        access = build_access_notice(paper)

    return f"""论文标题：{title}
论文页面：{url}
全文访问状态：{access}

可用证据：
{evidence}

用户问题：{question.strip()}
"""


def answer_rag_query(query_id: int) -> None:
    query = get_rag_query(query_id)
    if query is None:
        return
    paper = get_paper(query["paper_id"])
    if paper is None:
        update_rag_query(query_id, RAG_FAILED, error="论文记录不存在")
        return

    try:
        document = get_document(paper["id"])
        if document is None or document["index_status"] != INDEX_INDEXED:
            raise RuntimeError("论文尚未完成向量索引")
        evidence = retrieve_relevant_chunks(paper["id"], query["question"])
        if not evidence:
            raise RuntimeError("没有检索到与问题相关的全文片段，请换一种问法")
        prompt = build_rag_prompt(query["question"], dict(paper), evidence)
        answer, model = ask_deepseek(RAG_SYSTEM_PROMPT, prompt)
        update_rag_query(
            query_id,
            RAG_ANSWERED,
            answer=answer,
            evidence=evidence,
            model=model,
        )
    except Exception as exc:
        update_rag_query(query_id, RAG_FAILED, error=str(exc)[:500])

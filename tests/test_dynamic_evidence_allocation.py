from my_agent_project import context_service, parent_expansion_service


def _evidence(
    paper_id: int,
    chunk_id: int,
    estimated_tokens: int,
    *,
    score: float = 0.5,
) -> dict:
    item = {
        "paper_id": paper_id,
        "paper_title": f"Paper {paper_id}",
        "page_number": chunk_id,
        "chunk_id": chunk_id,
        "score": score,
        "content": "",
    }
    header_tokens = context_service.estimate_evidence_tokens(item)
    assert estimated_tokens > header_tokens
    character = chr(0x4E00 + ((paper_id * 31 + chunk_id) % 1000))
    item["content"] = character * (estimated_tokens - header_tokens)
    assert context_service.estimate_evidence_tokens(item) == estimated_tokens
    return item


def _pack(evidence: list[dict], budget: int = 6000) -> dict:
    return context_service.pack_research_context(
        evidence,
        config=context_service.ContextConfig(max_estimated_tokens=budget),
    )


def test_dynamic_allocation_preserves_mmr_input_priority_over_hybrid_score():
    mmr_first = _evidence(1, 1, 200, score=0.1)
    mmr_second = _evidence(1, 2, 200, score=0.9)

    packed = _pack([mmr_first, mmr_second], budget=1000)

    assert [item["chunk_id"] for item in packed["evidence"]] == [1, 2]


def test_dynamic_allocation_removes_fixed_two_chunks_per_paper_limit():
    evidence = [_evidence(1, chunk_id, 1000) for chunk_id in (1, 2, 3)]

    packed = _pack(evidence)

    assert [item["chunk_id"] for item in packed["evidence"]] == [1, 2, 3]
    assert packed["stats"]["per_paper"][0]["selected_chunks"] == 3


def test_single_paper_skips_oversized_middle_item_and_continues():
    evidence = [
        _evidence(1, 1, 2500),
        _evidence(1, 2, 2200),
        _evidence(1, 3, 1800),
        _evidence(1, 4, 900),
    ]

    packed = _pack(evidence)

    assert [item["chunk_id"] for item in packed["evidence"]] == [1, 2, 4]
    assert packed["stats"]["selected_tokens"] == 5600
    assert packed["stats"]["dropped_token_budget"] == 1


def test_coverage_gives_each_paper_one_short_evidence():
    evidence = [_evidence(paper_id, paper_id, 150) for paper_id in (1, 2, 3)]

    packed = _pack(evidence, budget=600)

    assert [item["paper_id"] for item in packed["evidence"]] == [1, 2, 3]
    assert packed["stats"]["coverage_selected"] == 3
    assert packed["stats"]["papers_without_evidence"] == 0


def test_long_first_paper_cannot_starve_later_papers():
    evidence = [
        _evidence(1, 1, 5000),
        _evidence(2, 2, 1000),
        _evidence(3, 3, 1000),
    ]

    packed = _pack(evidence)

    assert [item["paper_id"] for item in packed["evidence"]] == [2, 3]
    assert packed["stats"]["papers_with_evidence"] == 2
    assert packed["stats"]["per_paper"][0]["coverage_status"] == "deferred"


def test_coverage_tries_shorter_same_paper_candidate_after_long_first():
    evidence = [
        _evidence(1, 1, 1600),
        _evidence(1, 2, 400),
        _evidence(2, 3, 400),
    ]

    packed = _pack(evidence, budget=2000)

    assert [item["chunk_id"] for item in packed["evidence"]] == [2, 3]
    assert packed["stats"]["coverage_selected"] == 2
    assert packed["stats"]["per_paper"][0]["coverage_status"] == "selected"


def test_fill_continues_in_stable_cross_paper_round_robin():
    evidence = [
        _evidence(1, 1, 200),
        _evidence(1, 2, 200),
        _evidence(2, 3, 200),
        _evidence(2, 4, 200),
    ]

    packed = _pack(evidence, budget=1000)

    assert [item["chunk_id"] for item in packed["evidence"]] == [1, 3, 2, 4]
    assert packed["stats"]["coverage_selected"] == 2
    assert packed["stats"]["fill_selected"] == 2


def test_fill_uses_continue_instead_of_break_after_budget_rejection():
    evidence = [
        _evidence(1, 1, 600),
        _evidence(1, 2, 500),
        _evidence(1, 3, 300),
    ]

    packed = _pack(evidence, budget=1000)

    assert [item["chunk_id"] for item in packed["evidence"]] == [1, 3]
    assert packed["stats"]["dropped_token_budget"] == 1


def test_dedup_keeps_first_mmr_priority_even_with_lower_hybrid_score():
    first = _evidence(1, 1, 200, score=0.1)
    second = _evidence(1, 2, 200, score=0.9)
    second["content"] = first["content"]

    packed = _pack([first, second], budget=1000)

    assert [item["chunk_id"] for item in packed["evidence"]] == [1]
    assert packed["stats"]["dropped_duplicate"] == 1
    assert packed["stats"]["dropped"][0]["duplicate_of"]["chunk_id"] == 1


def test_parent_oversize_check_includes_the_same_evidence_header_as_packer():
    seed = {
        "paper_id": 1,
        "chunk_id": 10,
        "parent_chunk_id": 100,
        "page_number": 1,
        "content": "child",
    }
    parent = {
        100: {
            "id": 100,
            "paper_id": 1,
            "page_number": 1,
            "content": "x",
        }
    }

    expanded, diagnostics = parent_expansion_service.expand_seed_chunks_to_parents(
        [seed],
        paper_id=1,
        paper_title="T" * 200,
        max_estimated_tokens=50,
        parent_lookup=lambda _paper_id, _parent_ids: parent,
    )

    assert expanded[0]["content"] == "child"
    assert diagnostics["parent_expansion_details"][0]["action"] == (
        "fallback_oversized"
    )


def test_dynamic_allocation_diagnostics_do_not_store_evidence_text():
    packed = _pack([_evidence(1, 1, 200)], budget=500)
    stats = packed["stats"]

    assert stats["allocation_strategy"] == "dynamic_v1"
    assert stats["evidence_budget_tokens"] == 500
    assert stats["selected_tokens"] == 200
    assert stats["papers_with_candidates"] == 1
    assert stats["papers_with_evidence"] == 1
    assert stats["papers_without_evidence"] == 0
    assert stats["dropped_token_budget"] == 0
    assert stats["dropped_duplicate"] == 0
    assert "content" not in stats["per_paper"][0]

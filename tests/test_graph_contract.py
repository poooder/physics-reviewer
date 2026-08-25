from physics_reviewer.agents import (
    _calibrated_overall_score,
    _retrieval_query_for,
    build_review_graph,
    intake_agent,
    router_agent,
    rubric_scoring_agent,
    rule_extraction_agent,
)


def test_overall_score_limits_duplicate_holistic_penalty():
    dimensions = {
        "novelty": 2,
        "physics_correctness": 3,
        "method_rigor": 3,
        "reproducibility": 2,
        "citation_quality": 3,
        "writing_quality": 3,
    }

    score = _calibrated_overall_score({"overall_score": 25}, dimensions)

    assert score == 51


def test_overall_score_allows_small_holistic_adjustment():
    dimensions = {
        "novelty": 4,
        "physics_correctness": 4,
        "method_rigor": 4,
        "reproducibility": 4,
        "citation_quality": 4,
        "writing_quality": 4,
    }

    assert _calibrated_overall_score({"overall_score": 77}, dimensions) == 77


def test_review_graph_compiles():
    graph = build_review_graph()
    assert graph is not None


def test_retrieval_query_includes_agent_focus_keywords():
    state = {"title": "Entangled photon pairs", "sections": {"Methods": "Bell test setup"}}

    physics_query = _retrieval_query_for("physics_check", state)
    citation_query = _retrieval_query_for("citation_check", state)

    assert "dimensional consistency" in physics_query
    assert "citation coverage" in citation_query
    assert "Entangled photon pairs" in physics_query
    assert "Bell test setup" in physics_query


def test_retrieval_query_survives_non_dict_sections():
    state = {"title": "Entangled photon pairs", "sections": ["not", "a", "dict"]}

    query = _retrieval_query_for("physics_check", state)

    assert "Entangled photon pairs" in query


def test_intake_agent_warns_when_paper_text_is_truncated(monkeypatch):
    from physics_reviewer import agents, config

    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(
        qwen_api_key="x",
        qwen_base_url="https://example.com",
        qwen_model="m",
        qwen_embedding_model="e",
        qwen_temperature=0.2,
        max_paper_chars=10,
        literature_search_enabled=False,
        literature_search_limit=5,
        semantic_scholar_api_key="",
        vector_store_dir="chroma_store",
        literature_max_distance=0.8,
    ))
    monkeypatch.setattr(agents, "get_settings", config.get_settings)

    state = agents.intake_agent({"paper_text": "a" * 100, "findings": []})

    assert len(state["paper_text"]) == 10
    warnings = [f for f in state["findings"] if f["agent"] == "intake"]
    assert warnings and warnings[0]["status"] == "warning"
    assert "truncated" in warnings[0]["findings"][0]


def test_intake_agent_no_warning_when_not_truncated(monkeypatch):
    from physics_reviewer import agents, config

    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(
        qwen_api_key="x",
        qwen_base_url="https://example.com",
        qwen_model="m",
        qwen_embedding_model="e",
        qwen_temperature=0.2,
        max_paper_chars=1000,
        literature_search_enabled=False,
        literature_search_limit=5,
        semantic_scholar_api_key="",
        vector_store_dir="chroma_store",
        literature_max_distance=0.8,
    ))
    monkeypatch.setattr(agents, "get_settings", config.get_settings)

    state = agents.intake_agent({"paper_text": "short paper", "findings": []})

    assert not [f for f in state["findings"] if f["agent"] == "intake"]


def test_rule_router_limits_specialist_calls(monkeypatch):
    from physics_reviewer import agents, config

    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(
        router_max_specialist_calls=2,
    ))
    monkeypatch.setattr(agents, "get_settings", config.get_settings)
    state = rule_extraction_agent(
        {
            "paper_text": (
                "Introduction\nWe propose a novel quantum photon experiment.\n"
                "Methods\nThe simulation uses a Hamiltonian H = E.\n"
                "References\n[1] Prior work"
            ),
            "findings": [],
        }
    )

    routed = router_agent(state)

    assert routed["selected_agents"] == ["physics_check", "novelty_check"]
    assert any(item["agent"] == "router" for item in routed["findings"])


def test_calibrated_rubric_requires_evidence_for_lowest_scores(monkeypatch):
    from physics_reviewer import agents, config

    class FakeQwen:
        def __init__(self):
            self.system = ""

        def complete_json(self, system, user):
            self.system = system
            return {
                "novelty": 3,
                "physics_correctness": 3,
                "method_rigor": 3,
                "reproducibility": 3,
                "citation_quality": 3,
                "writing_quality": 3,
                "overall_score": 60,
            }

    fake_qwen = FakeQwen()
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(literature_search_enabled=False))
    monkeypatch.setattr(agents, "get_settings", config.get_settings)
    monkeypatch.setattr(agents, "_qwen", lambda: fake_qwen)

    state = rubric_scoring_agent(
        {"review_id": "review-1", "title": "T", "findings": [], "sections": {}}
    )

    assert state["scores"]["overall_score"] == 60
    assert "3=adequate baseline" in fake_qwen.system
    assert "specific paper quote/equation" in fake_qwen.system

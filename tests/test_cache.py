from physics_reviewer import agents, cache_store, knowledge_store, literature, pdf_parser
from physics_reviewer.config import Settings
from physics_reviewer.schemas import ReviewResponse


def _review_payload() -> dict:
    return {
        "report": {
            "title": "Cached paper",
            "scores": {
                "novelty": 3,
                "physics_correctness": 3,
                "method_rigor": 3,
                "reproducibility": 3,
                "citation_quality": 3,
                "writing_quality": 3,
                "overall_score": 60,
            },
            "strengths": [],
            "weaknesses": [],
            "required_checks": [],
            "uncertainty_notes": [],
            "summary": "Cached result",
        },
        "findings": [],
        "literature": [],
    }


def _memory_cache(monkeypatch, module):
    values = {}
    monkeypatch.setattr(module, "get_cached", lambda namespace, key, *args: values.get((namespace, key)))
    monkeypatch.setattr(module, "set_cached", lambda namespace, key, value: values.__setitem__((namespace, key), value))
    return values


def test_sqlite_cache_persists_and_expires(monkeypatch, tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'cache.db').as_posix()}")
    monkeypatch.setattr(cache_store, "get_settings", lambda: settings)

    cache_store.set_cached("test", "key", {"value": 1})

    assert cache_store.get_cached("test", "key") == {"value": 1}
    assert cache_store.get_cached("test", "key", max_age_seconds=-1) is None


def test_nested_cache_namespaces_never_share_a_lock():
    review_lock = cache_store._cache_lock_for("paper_review", "same-key")
    embedding_lock = cache_store._cache_lock_for("embedding:model", "same-key")

    assert review_lock is not embedding_lock


def test_pdf_extraction_is_cached_by_content_hash(monkeypatch):
    _memory_cache(monkeypatch, pdf_parser)
    calls = []

    def fake_extract(content):
        calls.append(content)
        return "extracted paper text"

    monkeypatch.setattr(pdf_parser, "extract_pdf_text_from_bytes", fake_extract)

    first = pdf_parser.extract_pdf_text_cached(b"same-pdf")
    second = pdf_parser.extract_pdf_text_cached(b"same-pdf")

    assert first == second == "extracted paper text"
    assert calls == [b"same-pdf"]


def test_identical_paper_reuses_complete_review(monkeypatch):
    _memory_cache(monkeypatch, agents)
    monkeypatch.setattr(agents, "get_settings", lambda: Settings(literature_search_enabled=False))
    calls = []

    def fake_review(title, text):
        calls.append((title, text))
        return ReviewResponse.model_validate(_review_payload())

    monkeypatch.setattr(agents, "_review_paper_uncached", fake_review)

    first = agents.review_paper("Cached paper", "identical content")
    second = agents.review_paper("Renamed copy", "identical content")

    assert first.report.summary == second.report.summary == "Cached result"
    assert second.report.title == "Renamed copy"
    assert calls == [("Cached paper", "identical content")]


def test_literature_search_reuses_cached_results(monkeypatch):
    _memory_cache(monkeypatch, literature)
    monkeypatch.setattr(literature, "get_settings", lambda: Settings(literature_search_limit=5))
    calls = []

    def fake_arxiv(query, limit):
        calls.append((query, limit))
        return [
            literature.LiteraturePaper(
                source="arxiv",
                title="A cached physics paper",
                external_id="1234.5678",
            )
        ]

    monkeypatch.setattr(literature, "_search_arxiv", fake_arxiv)
    monkeypatch.setattr(literature, "_search_semantic_scholar", lambda query, limit: [])

    first = literature.search_literature(" Quantum   transport ", 5)
    second = literature.search_literature("quantum transport", 5)

    assert [paper.title for paper in first] == [paper.title for paper in second]
    assert calls == [(" Quantum   transport ", 5)]


def test_embedding_cache_only_requests_missing_unique_texts(monkeypatch):
    _memory_cache(monkeypatch, knowledge_store)
    store = knowledge_store.KnowledgeStore.__new__(knowledge_store.KnowledgeStore)
    store._embedding_model = "embedding-model"
    store._settings_base_url = "https://example.com/v1"
    calls = []

    def fake_embed(texts):
        calls.append(texts)
        return [[float(len(text)), 1.0] for text in texts]

    store._embed_uncached = fake_embed

    first = store.embed(["alpha", "beta", "alpha"])
    second = store.embed(["alpha", "gamma"])

    assert first[0] == first[2]
    assert second[0] == first[0]
    assert calls == [["alpha", "beta"], ["gamma"]]

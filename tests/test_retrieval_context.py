from physics_reviewer.agents import _format_retrieved_literature


def _row(title: str, distance: float | None) -> dict:
    return {
        "id": title,
        "document": f"Title: {title}\nAbstract: ...",
        "metadata": {"title": title, "year": 2024, "source": "arxiv", "url": "https://example.com"},
        "distance": distance,
    }


def test_filters_out_rows_beyond_max_distance():
    rows = [_row("close match", 0.2), _row("far match", 0.95)]

    context = _format_retrieved_literature(rows, max_distance=0.8)

    assert "close match" in context
    assert "far match" not in context


def test_returns_message_when_nothing_survives_threshold():
    rows = [_row("far match", 0.95)]

    context = _format_retrieved_literature(rows, max_distance=0.8)

    assert context == "No sufficiently relevant external literature found for this check."


def test_treats_missing_distance_as_always_relevant():
    rows = [_row("unknown distance", None)]

    context = _format_retrieved_literature(rows, max_distance=0.8)

    assert "unknown distance" in context
    assert "[similarity=unknown]" in context

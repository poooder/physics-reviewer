from fastapi.testclient import TestClient

from physics_reviewer.api import app
from physics_reviewer.schemas import (
    AgentFinding,
    BatchStatusResponse,
    ReviewReport,
    ReviewResponse,
    ReviewTaskCreated,
    ReviewTaskStatusResponse,
    ScoreCard,
)

client = TestClient(app)


def _fake_response() -> ReviewResponse:
    return ReviewResponse(
        report=ReviewReport(
            title="Test Paper",
            scores=ScoreCard(
                novelty=3,
                physics_correctness=3,
                method_rigor=3,
                reproducibility=3,
                citation_quality=3,
                writing_quality=3,
                overall_score=60,
            ),
            strengths=["clear writing"],
            weaknesses=["limited data"],
            required_checks=[],
            uncertainty_notes=[],
            summary="A solid paper.",
        ),
        findings=[AgentFinding(agent="physics_check", status="ok", findings=["fine"], evidence=[])],
    )


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_uses_versioned_static_assets():
    response = client.get("/")

    assert response.status_code == 200
    assert "/static/app.js?v=" in response.text
    assert "/static/styles.css?v=" in response.text
    assert "graph-summary" not in response.text


def test_review_text_returns_review(monkeypatch):
    monkeypatch.setattr("physics_reviewer.api.review_paper", lambda title, text: _fake_response())

    response = client.post(
        "/reviews/text", json={"title": "T", "paper_text": "Some physics content."}
    )

    assert response.status_code == 200
    assert response.json()["report"]["summary"] == "A solid paper."


def test_review_text_failure_does_not_leak_exception_detail(monkeypatch):
    def _boom(title, text):
        raise RuntimeError("dashscope api key sk-secret-internal-value")

    monkeypatch.setattr("physics_reviewer.api.review_paper", _boom)

    response = client.post("/reviews/text", json={"title": "T", "paper_text": "x"})

    assert response.status_code == 500
    assert "sk-secret-internal-value" not in response.text


def test_review_pdf_rejects_non_pdf_upload():
    response = client.post(
        "/reviews/pdf", files={"file": ("notes.txt", b"not a pdf", "text/plain")}
    )

    assert response.status_code == 400


def test_async_pdf_upload_returns_task(monkeypatch):
    monkeypatch.setattr(
        "physics_reviewer.api.submit_pdf_review",
        lambda filename, content, batch_id=None: ReviewTaskCreated(
            task_id="task-1", batch_id=batch_id, status="queued", filename=filename
        ),
    )

    response = client.post(
        "/reviews/pdf/async", files={"file": ("paper.pdf", b"%PDF-test", "application/pdf")}
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-1"


def test_batch_pdf_upload_returns_batch(monkeypatch):
    monkeypatch.setattr("physics_reviewer.api.create_batch_id", lambda: "batch-1")
    monkeypatch.setattr(
        "physics_reviewer.api.submit_pdf_review",
        lambda filename, content, batch_id=None: ReviewTaskCreated(
            task_id=f"task-{filename}", batch_id=batch_id, status="queued", filename=filename
        ),
    )

    response = client.post(
        "/batches/pdf",
        files=[
            ("files", ("a.pdf", b"%PDF-a", "application/pdf")),
            ("files", ("b.pdf", b"%PDF-b", "application/pdf")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["batch_id"] == "batch-1"
    assert len(response.json()["tasks"]) == 2


def test_task_status_returns_404_when_missing(monkeypatch):
    monkeypatch.setattr("physics_reviewer.api.db.get_task", lambda task_id: None)

    response = client.get("/tasks/missing")

    assert response.status_code == 404


def test_batch_status_returns_counts(monkeypatch):
    task = ReviewTaskStatusResponse(
        task_id="task-1",
        batch_id="batch-1",
        filename="paper.pdf",
        status="succeeded",
        result=_fake_response(),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    monkeypatch.setattr(
        "physics_reviewer.api.get_batch_status",
        lambda batch_id: BatchStatusResponse(
            batch_id=batch_id,
            total=1,
            queued=0,
            running=0,
            succeeded=1,
            failed=0,
            tasks=[task],
        ),
    )

    response = client.get("/batches/batch-1")

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1


def test_batch_export_rejects_missing_batch(monkeypatch):
    monkeypatch.setattr(
        "physics_reviewer.api.get_batch_status",
        lambda batch_id: BatchStatusResponse(
            batch_id=batch_id, total=0, queued=0, running=0, succeeded=0, failed=0, tasks=[]
        ),
    )

    response = client.get("/batches/missing/export?format=csv")

    assert response.status_code == 404


def test_batch_export_csv(monkeypatch):
    task = ReviewTaskStatusResponse(
        task_id="task-1",
        batch_id="batch-1",
        filename="paper.pdf",
        status="succeeded",
        result=_fake_response(),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    monkeypatch.setattr(
        "physics_reviewer.api.get_batch_status",
        lambda batch_id: BatchStatusResponse(
            batch_id=batch_id, total=1, queued=0, running=0, succeeded=1, failed=0, tasks=[task]
        ),
    )

    response = client.get("/batches/batch-1/export?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "overall_score" in response.text


def test_batch_export_xlsx(monkeypatch):
    task = ReviewTaskStatusResponse(
        task_id="task-1",
        batch_id="batch-1",
        filename="paper.pdf",
        status="succeeded",
        result=_fake_response(),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    monkeypatch.setattr(
        "physics_reviewer.api.get_batch_status",
        lambda batch_id: BatchStatusResponse(
            batch_id=batch_id, total=1, queued=0, running=0, succeeded=1, failed=0, tasks=[task]
        ),
    )

    response = client.get("/batches/batch-1/export?format=xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content.startswith(b"PK")

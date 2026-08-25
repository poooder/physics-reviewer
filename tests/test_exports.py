from io import BytesIO
from zipfile import ZipFile

from physics_reviewer.exports import render_batch_csv, render_batch_xlsx
from physics_reviewer.schemas import (
    BatchStatusResponse,
    ReviewReport,
    ReviewResponse,
    ReviewTaskStatusResponse,
    ScoreCard,
)


def _batch() -> BatchStatusResponse:
    report = ReviewReport(
        title="Quantum Test",
        scores=ScoreCard(
            novelty=4,
            physics_correctness=5,
            method_rigor=3,
            reproducibility=2,
            citation_quality=4,
            writing_quality=3,
            overall_score=78,
        ),
        strengths=["clear result"],
        weaknesses=["limited sample"],
        required_checks=["verify units"],
        uncertainty_notes=[],
        summary="Useful paper.",
    )
    tasks = [
        ReviewTaskStatusResponse(
            task_id="success-1",
            batch_id="batch-1",
            filename="quantum.pdf",
            status="succeeded",
            result=ReviewResponse(report=report, findings=[]),
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:01+00:00",
        ),
        ReviewTaskStatusResponse(
            task_id="failed-1",
            batch_id="batch-1",
            filename="bad.pdf",
            status="failed",
            error="No extractable text",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:01+00:00",
        ),
    ]
    return BatchStatusResponse(
        batch_id="batch-1", total=2, queued=0, running=0, succeeded=1, failed=1, tasks=tasks
    )


def test_csv_export_contains_scores_and_errors():
    content = render_batch_csv(_batch()).decode("utf-8-sig")

    assert "overall_score" in content
    assert "78" in content
    assert "No extractable text" in content


def test_xlsx_export_is_a_valid_workbook_zip():
    content = render_batch_xlsx(_batch())

    with ZipFile(BytesIO(content)) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Quantum Test" in sheet
    assert "overall_score" in sheet

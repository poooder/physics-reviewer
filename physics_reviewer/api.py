import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from physics_reviewer.agents import review_paper
from physics_reviewer.literature import search_literature
from physics_reviewer.pdf_parser import extract_pdf_text_cached
from physics_reviewer.exports import render_batch_csv, render_batch_xlsx
from physics_reviewer.schemas import (
    BatchCreated,
    BatchStatusResponse,
    PaperInput,
    ReviewResponse,
    ReviewTaskCreated,
    ReviewTaskStatusResponse,
)
from physics_reviewer.tasks import create_batch_id, get_batch_status, init_task_backend, submit_pdf_review
from physics_reviewer import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_task_backend()
    yield


app = FastAPI(title="Physics Reviewer", version="0.1.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reviews/text", response_model=ReviewResponse)
def review_text(payload: PaperInput) -> ReviewResponse:
    try:
        return review_paper(payload.title, payload.paper_text)
    except Exception as exc:
        logger.exception("Failed to review paper text")
        raise HTTPException(
            status_code=500, detail="Failed to generate review. See server logs for details."
        ) from exc


@app.get("/literature/search")
def literature_search(q: str, limit: int = 5) -> dict:
    try:
        papers = search_literature(q, limit)
        return {
            "query": q,
            "papers": [paper.model_dump() for paper in papers],
        }
    except Exception as exc:
        logger.exception("Literature search failed for query=%r", q)
        raise HTTPException(
            status_code=500, detail="Literature search failed. See server logs for details."
        ) from exc


@app.post("/reviews/pdf", response_model=ReviewResponse)
async def review_pdf(file: UploadFile = File(...)) -> ReviewResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        text = extract_pdf_text_cached(await file.read())
        if not text.strip():
            raise HTTPException(status_code=400, detail="No extractable text found in PDF.")
        return review_paper(Path(file.filename).stem, text)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to review uploaded PDF %r", file.filename)
        raise HTTPException(
            status_code=500, detail="Failed to generate review. See server logs for details."
        ) from exc


@app.post("/reviews/pdf/async", response_model=ReviewTaskCreated)
async def review_pdf_async(file: UploadFile = File(...)) -> ReviewTaskCreated:
    _validate_pdf(file)
    return submit_pdf_review(file.filename or "paper.pdf", await file.read())


@app.post("/batches/pdf", response_model=BatchCreated)
async def create_pdf_batch(files: list[UploadFile] = File(...)) -> BatchCreated:
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF file is required.")

    batch_id = create_batch_id()
    tasks = []
    for file in files:
        _validate_pdf(file)
        tasks.append(submit_pdf_review(file.filename or "paper.pdf", await file.read(), batch_id=batch_id))
    return BatchCreated(batch_id=batch_id, tasks=tasks)


@app.get("/tasks/{task_id}", response_model=ReviewTaskStatusResponse)
def get_review_task(task_id: str) -> ReviewTaskStatusResponse:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@app.get("/batches/{batch_id}", response_model=BatchStatusResponse)
def get_review_batch(batch_id: str) -> BatchStatusResponse:
    batch = get_batch_status(batch_id)
    if batch.total == 0:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return batch


@app.get("/batches/{batch_id}/export")
def export_review_batch(batch_id: str, format: str = "xlsx") -> Response:
    batch = get_batch_status(batch_id)
    if batch.total == 0:
        raise HTTPException(status_code=404, detail="Batch not found.")

    if format == "csv":
        payload = render_batch_csv(batch)
        media_type = "text/csv; charset=utf-8"
        filename = f"physics-review-{batch_id}.csv"
    elif format == "xlsx":
        payload = render_batch_xlsx(batch)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"physics-review-{batch_id}.xlsx"
    else:
        raise HTTPException(status_code=400, detail="format must be csv or xlsx.")

    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _validate_pdf(file: UploadFile) -> None:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

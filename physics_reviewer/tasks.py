import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from physics_reviewer import db
from physics_reviewer.agents import review_paper
from physics_reviewer.config import get_settings
from physics_reviewer.pdf_parser import extract_pdf_text_cached
from physics_reviewer.schemas import BatchStatusResponse, ReviewTaskCreated


logger = logging.getLogger(__name__)
_executor: ThreadPoolExecutor | None = None


def init_task_backend() -> None:
    global _executor
    db.init_db()
    if _executor is None:
        interrupted = db.fail_incomplete_tasks()
        if interrupted:
            logger.warning("Marked %s interrupted task(s) as failed during startup", interrupted)
        _executor = ThreadPoolExecutor(max_workers=get_settings().task_worker_count)


def create_batch_id() -> str:
    return uuid.uuid4().hex


def submit_pdf_review(filename: str, content: bytes, batch_id: str | None = None) -> ReviewTaskCreated:
    if _executor is None:
        init_task_backend()

    task_id = uuid.uuid4().hex
    title = Path(filename).stem
    db.create_task(task_id=task_id, batch_id=batch_id, filename=filename, title=title)
    assert _executor is not None
    _executor.submit(_run_pdf_task, task_id, filename, content)
    return ReviewTaskCreated(task_id=task_id, batch_id=batch_id, status="queued", filename=filename)


def get_batch_status(batch_id: str) -> BatchStatusResponse:
    tasks = db.list_batch(batch_id)
    counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
    for task in tasks:
        counts[task.status] += 1
    return BatchStatusResponse(batch_id=batch_id, total=len(tasks), tasks=tasks, **counts)


def _run_pdf_task(task_id: str, filename: str, content: bytes) -> None:
    db.mark_running(task_id)
    try:
        text = extract_pdf_text_cached(content)
        if not text.strip():
            raise ValueError("No extractable text found in PDF.")

        result = review_paper(Path(filename).stem, text)
        db.mark_succeeded(task_id, result)
    except Exception as exc:
        logger.exception("Review task %s failed for %r", task_id, filename)
        db.mark_failed(task_id, _safe_error(exc))


def _safe_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if "sk-" in text:
        return "Review failed because an upstream API call failed."
    return text[:500]

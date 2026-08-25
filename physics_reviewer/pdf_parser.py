import hashlib
import logging
from pathlib import Path

import fitz

from physics_reviewer.cache_store import cache_key, cache_lock, get_cached, set_cached


PDF_EXTRACTOR_VERSION = "pymupdf-text-v1"
logger = logging.getLogger(__name__)


def extract_pdf_text(path: str | Path) -> str:
    document = fitz.open(path)
    try:
        pages = [page.get_text("text") for page in document]
    finally:
        document.close()
    return "\n\n".join(page.strip() for page in pages if page.strip())


def extract_pdf_text_from_bytes(content: bytes) -> str:
    document = fitz.open(stream=content, filetype="pdf")
    try:
        pages = [page.get_text("text") for page in document]
    finally:
        document.close()
    return "\n\n".join(page.strip() for page in pages if page.strip())


def extract_pdf_text_cached(content: bytes) -> str:
    key = cache_key(
        {
            "version": PDF_EXTRACTOR_VERSION,
            "content_hash": hashlib.sha256(content).hexdigest(),
        }
    )
    cached = get_cached("pdf_text", key)
    if isinstance(cached, str):
        logger.info("PDF extraction cache hit key=%s", key[:12])
        return cached

    with cache_lock("pdf_text", key):
        cached = get_cached("pdf_text", key)
        if isinstance(cached, str):
            logger.info("PDF extraction cache hit after wait key=%s", key[:12])
            return cached
        text = extract_pdf_text_from_bytes(content)
        set_cached("pdf_text", key, text)
        return text

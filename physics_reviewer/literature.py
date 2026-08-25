import hashlib
import json
import logging
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

import certifi

from physics_reviewer.config import get_settings
from physics_reviewer.cache_store import cache_key, cache_lock, get_cached, set_cached
from physics_reviewer.schemas import LiteraturePaper

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
LITERATURE_CACHE_VERSION = "literature-search-v1"


def build_literature_query(title: str | None, paper_text: str) -> str:
    if title:
        return title[:220]

    abstract_match = re.search(r"(?is)\babstract\b[:.\s]*(.{120,1200})", paper_text)
    source = abstract_match.group(1) if abstract_match else paper_text[:1200]
    keywords = _keywords(source, limit=10)
    return " ".join(keywords) or source[:220]


def search_literature(query: str, limit: int | None = None) -> list[LiteraturePaper]:
    settings = get_settings()
    size = limit or settings.literature_search_limit
    normalized_query = re.sub(r"\s+", " ", query).strip().lower()
    key = cache_key(
        {
            "version": LITERATURE_CACHE_VERSION,
            "query": normalized_query,
            "limit": size,
        }
    )
    cached = get_cached("literature_search", key, settings.literature_cache_ttl_seconds)
    papers = _papers_from_cache(cached)
    if papers is not None:
        logger.info("Literature search cache hit query=%r limit=%s", normalized_query, size)
        return papers

    with cache_lock("literature_search", key):
        cached = get_cached("literature_search", key, settings.literature_cache_ttl_seconds)
        papers = _papers_from_cache(cached)
        if papers is not None:
            logger.info("Literature search cache hit after wait query=%r limit=%s", normalized_query, size)
            return papers

        papers = _search_literature_uncached(query, size)
        set_cached(
            "literature_search",
            key,
            [paper.model_dump(mode="json") for paper in papers],
        )
        return papers


def _search_literature_uncached(query: str, size: int) -> list[LiteraturePaper]:
    papers: list[LiteraturePaper] = []

    for search_fn in (_search_arxiv, _search_semantic_scholar):
        try:
            papers.extend(search_fn(query, size))
        except Exception:
            logger.warning("Literature search via %s failed", search_fn.__name__, exc_info=True)
            continue

    return _dedupe_papers(papers)[:size]


def _papers_from_cache(value: Any) -> list[LiteraturePaper] | None:
    if not isinstance(value, list):
        return None
    try:
        return [LiteraturePaper.model_validate(item) for item in value]
    except Exception:
        return None


def literature_context(papers: list[LiteraturePaper]) -> str:
    rows = []
    for index, paper in enumerate(papers, start=1):
        authors = ", ".join(paper.authors[:4]) or "unknown authors"
        rows.append(
            f"[{index}] {paper.title} ({paper.year or 'n.d.'}, {paper.source})\n"
            f"Authors: {authors}\n"
            f"Citations: {paper.citation_count if paper.citation_count is not None else 'unknown'}\n"
            f"URL: {paper.url or 'n/a'}\n"
            f"Abstract: {(paper.abstract or '')[:900]}"
        )

    return "\n\n".join(rows)


def _search_arxiv(query: str, limit: int) -> list[LiteraturePaper]:
    params = urllib.parse.urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    with urllib.request.urlopen(
        f"{ARXIV_API}?{params}",
        timeout=15,
        context=_ssl_context(),
    ) as response:
        data = response.read()

    root = ET.fromstring(data)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("atom:entry", ns):
        title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns))
        abstract = _clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
        published = entry.findtext("atom:published", default="", namespaces=ns)
        url = entry.findtext("atom:id", default="", namespaces=ns)
        authors = [
            _clean_text(author.findtext("atom:name", default="", namespaces=ns))
            for author in entry.findall("atom:author", ns)
        ]
        papers.append(
            LiteraturePaper(
                source="arxiv",
                title=title,
                abstract=abstract,
                year=_parse_year(published),
                url=url,
                authors=[author for author in authors if author],
                external_id=url.rsplit("/", 1)[-1] if url else None,
            )
        )
    return papers


def _search_semantic_scholar(query: str, limit: int) -> list[LiteraturePaper]:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "limit": limit,
            "fields": "title,abstract,year,url,authors,citationCount,externalIds",
        }
    )
    headers = {"User-Agent": "physics-reviewer/0.1"}
    api_key = get_settings().semantic_scholar_api_key
    if api_key:
        headers["x-api-key"] = api_key

    request = urllib.request.Request(f"{SEMANTIC_SCHOLAR_API}?{params}", headers=headers)
    with urllib.request.urlopen(request, timeout=15, context=_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))

    papers = []
    for item in payload.get("data", []):
        title = _clean_text(item.get("title") or "")
        if not title:
            continue
        external_ids = item.get("externalIds") or {}
        papers.append(
            LiteraturePaper(
                source="semantic_scholar",
                title=title,
                abstract=_clean_text(item.get("abstract") or "") or None,
                year=item.get("year"),
                url=item.get("url"),
                authors=[
                    _clean_text(author.get("name") or "")
                    for author in item.get("authors", [])
                    if author.get("name")
                ],
                citation_count=item.get("citationCount"),
                external_id=external_ids.get("DOI")
                or external_ids.get("ArXiv")
                or item.get("paperId"),
            )
        )
    return papers


def _dedupe_papers(papers: list[LiteraturePaper]) -> list[LiteraturePaper]:
    seen: set[str] = set()
    unique = []
    for paper in papers:
        key = _fingerprint(paper.external_id or paper.title)
        if key in seen or not paper.title:
            continue
        seen.add(key)
        unique.append(paper)
    return unique


def _keywords(text: str, limit: int) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "paper",
        "study",
        "using",
        "show",
        "shows",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text.lower())
    counts = Counter(word for word in words if word not in stopwords)
    return [word for word, _ in counts.most_common(limit)]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest()


def _parse_year(value: Any) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", str(value))
    return int(match.group(0)) if match else None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())

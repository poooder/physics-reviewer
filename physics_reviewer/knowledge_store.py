import hashlib
import logging
import os
from functools import lru_cache
from typing import Any

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb
from chromadb.config import Settings as ChromaSettings
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from physics_reviewer.config import get_settings
from physics_reviewer.cache_store import cache_key, cache_lock, get_cached, set_cached
from physics_reviewer.schemas import LiteraturePaper


logger = logging.getLogger(__name__)


class KnowledgeStore:
    def __init__(self, collection_name: str) -> None:
        settings = get_settings()
        self._client = _chroma_client(settings.vector_store_dir)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={
                "description": "External physics literature search results",
                "hnsw:space": "cosine",
            },
        )
        self._openai = OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            timeout=settings.embedding_request_timeout_seconds,
            max_retries=0,
        )
        self._embedding_model = settings.qwen_embedding_model
        self._settings_base_url = settings.qwen_base_url

    def upsert_papers(self, papers: list[LiteraturePaper]) -> None:
        if not papers:
            return

        documents = [_paper_document(paper) for paper in papers]
        ids = [_paper_id(paper) for paper in papers]
        embeddings = self.embed(documents)
        metadatas = [_paper_metadata(paper) for paper in papers]
        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def query(self, text: str, top_k: int = 5) -> list[dict[str, Any]]:
        embedding = self.embed([text])[0]
        result = self._collection.query(query_embeddings=[embedding], n_results=top_k)
        rows = []
        for index, doc_id in enumerate(result.get("ids", [[]])[0]):
            rows.append(
                {
                    "id": doc_id,
                    "document": result.get("documents", [[]])[0][index],
                    "metadata": result.get("metadatas", [[]])[0][index],
                    "distance": result.get("distances", [[]])[0][index],
                }
            )
        return rows

    def delete_collection(self) -> None:
        self._client.delete_collection(self._collection.name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        namespace = f"embedding:{cache_key({'model': self._embedding_model, 'base_url': self._settings_base_url})[:20]}"
        results: list[list[float] | None] = [None] * len(texts)
        missing: dict[str, dict[str, Any]] = {}
        cache_hits = 0

        for index, text in enumerate(texts):
            key = cache_key({"text": text})
            cached = get_cached(namespace, key)
            if _valid_embedding(cached):
                results[index] = cached
                cache_hits += 1
            else:
                entry = missing.setdefault(key, {"text": text, "indices": []})
                entry["indices"].append(index)

        if missing:
            with cache_lock(namespace, "embedding_batch"):
                uncached_entries: list[tuple[str, dict[str, Any]]] = []
                for key, entry in missing.items():
                    cached = get_cached(namespace, key)
                    if _valid_embedding(cached):
                        for index in entry["indices"]:
                            results[index] = cached
                    else:
                        uncached_entries.append((key, entry))

                if uncached_entries:
                    embeddings = self._embed_uncached(
                        [entry["text"] for _, entry in uncached_entries]
                    )
                    if len(embeddings) != len(uncached_entries):
                        raise RuntimeError("Embedding API returned an unexpected result count.")
                    for (key, entry), embedding in zip(uncached_entries, embeddings):
                        set_cached(namespace, key, embedding)
                        for index in entry["indices"]:
                            results[index] = embedding

        if any(item is None for item in results):
            raise RuntimeError("Embedding cache failed to resolve all requested texts.")
        logger.info(
            "Embedding cache resolved %s/%s input(s); requested %s unique missing vector(s)",
            cache_hits,
            len(texts),
            len(missing),
        )
        return [item for item in results if item is not None]

    def _embed_uncached(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()
        retrying = retry(
            wait=wait_exponential(multiplier=1, min=1, max=8),
            stop=stop_after_attempt(max(1, settings.qwen_retry_attempts)),
            reraise=True,
        )
        response = retrying(self._openai.embeddings.create)(
            model=self._embedding_model, input=texts
        )
        return [item.embedding for item in response.data]


@lru_cache
def _chroma_client(path: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


@lru_cache
def get_knowledge_store(review_id: str) -> KnowledgeStore:
    return KnowledgeStore(collection_name=f"lit_{review_id}")


def _paper_document(paper: LiteraturePaper) -> str:
    authors = ", ".join(paper.authors[:8])
    return (
        f"Title: {paper.title}\n"
        f"Authors: {authors}\n"
        f"Year: {paper.year or 'unknown'}\n"
        f"Source: {paper.source}\n"
        f"Abstract: {paper.abstract or ''}"
    )


def _paper_metadata(paper: LiteraturePaper) -> dict[str, str | int | float | bool | None]:
    return {
        "source": paper.source,
        "title": paper.title,
        "year": paper.year,
        "url": paper.url,
        "citation_count": paper.citation_count,
        "external_id": paper.external_id,
    }


def _paper_id(paper: LiteraturePaper) -> str:
    key = paper.external_id or paper.url or paper.title
    digest = hashlib.sha256(key.lower().encode("utf-8")).hexdigest()[:24]
    return f"{paper.source}:{digest}"


def _valid_embedding(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, (int, float)) for item in value
    )

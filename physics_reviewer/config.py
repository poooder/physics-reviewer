from functools import lru_cache
import os

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()


class Settings(BaseModel):
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"
    qwen_embedding_model: str = "text-embedding-v4"
    qwen_temperature: float = 0.2
    max_paper_chars: int = 120000
    literature_search_enabled: bool = True
    literature_search_limit: int = 5
    semantic_scholar_api_key: str = ""
    vector_store_dir: str = "chroma_store"
    literature_max_distance: float = 0.8
    database_url: str = "sqlite:///physics_reviewer.db"
    task_worker_count: int = 2
    router_max_specialist_calls: int = 3
    cache_enabled: bool = True
    literature_cache_ttl_seconds: int = 86400
    review_cache_ttl_seconds: int = 2592000
    qwen_request_timeout_seconds: float = 120.0
    qwen_retry_attempts: int = 2
    embedding_request_timeout_seconds: float = 45.0


@lru_cache
def get_settings() -> Settings:
    return Settings(
        qwen_api_key=os.getenv("QWEN_API_KEY", ""),
        qwen_base_url=os.getenv(
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
        qwen_embedding_model=os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        qwen_temperature=float(os.getenv("QWEN_TEMPERATURE", "0.2")),
        max_paper_chars=int(os.getenv("MAX_PAPER_CHARS", "120000")),
        literature_search_enabled=os.getenv("LITERATURE_SEARCH_ENABLED", "true").lower()
        == "true",
        literature_search_limit=int(os.getenv("LITERATURE_SEARCH_LIMIT", "5")),
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", ""),
        vector_store_dir=os.getenv("VECTOR_STORE_DIR", "chroma_store"),
        literature_max_distance=float(os.getenv("LITERATURE_MAX_DISTANCE", "0.8")),
        database_url=os.getenv("DATABASE_URL", "sqlite:///physics_reviewer.db"),
        task_worker_count=int(os.getenv("TASK_WORKER_COUNT", "2")),
        router_max_specialist_calls=int(os.getenv("ROUTER_MAX_SPECIALIST_CALLS", "3")),
        cache_enabled=os.getenv("CACHE_ENABLED", "true").lower() == "true",
        literature_cache_ttl_seconds=int(
            os.getenv("LITERATURE_CACHE_TTL_SECONDS", "86400")
        ),
        review_cache_ttl_seconds=int(
            os.getenv("REVIEW_CACHE_TTL_SECONDS", "2592000")
        ),
        qwen_request_timeout_seconds=float(
            os.getenv("QWEN_REQUEST_TIMEOUT_SECONDS", "120")
        ),
        qwen_retry_attempts=int(os.getenv("QWEN_RETRY_ATTEMPTS", "2")),
        embedding_request_timeout_seconds=float(
            os.getenv("EMBEDDING_REQUEST_TIMEOUT_SECONDS", "45")
        ),
    )

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_ROOT.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_BACKEND_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODECOMPASS_",
        env_file=(".env", "../.env"),
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = "sqlite+aiosqlite:///./data/codecompass.db"
    upload_max_bytes: int = 50 * 1024 * 1024
    expanded_max_bytes: int = 200 * 1024 * 1024
    zip_max_entries: int = 20_000
    zip_entry_max_bytes: int = 5 * 1024 * 1024
    zip_max_compression_ratio: float = 100.0
    allowed_extensions: list[str] = [
        ".py",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ini",
        ".cfg",
        ".sql",
        ".sh",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".rb",
        ".kt",
        ".cs",
    ]
    allowed_extensionless_files: list[str] = [
        "Dockerfile",
        "Makefile",
        "Procfile",
    ]
    excluded_directories: list[str] = [
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "coverage",
        "__pycache__",
    ]
    chunk_max_lines: int = 120
    chunk_overlap_lines: int = 20
    worker_poll_seconds: float = 0.1
    worker_lease_seconds: int = 30
    worker_max_attempts: int = 3
    worker_retry_base_seconds: float = 0.25
    background_worker_enabled: bool = True
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:5173",
    ]
    vector_backend: Literal["qdrant", "fake", "disabled"] = "qdrant"
    embedding_backend: Literal["sentence_transformers", "fake"] = "sentence_transformers"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "codecompass_chunks"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_model_revision: str = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    embedding_dimensions: int = 384
    embedding_distance: Literal["cosine"] = "cosine"
    embedding_normalize: bool = True
    embedding_pooling: str = "model-default"
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""
    embedding_max_input_tokens: int = 256
    embedding_preprocessing_version: str = "v1"
    llm_backend: Literal["ollama", "bedrock", "fake"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:3b"
    bedrock_model_id: str = ""
    bedrock_region: str = "us-east-1"
    bedrock_max_tokens: int = 1024
    bedrock_temperature: float = 0.0
    bedrock_timeout_seconds: float = 60.0
    bedrock_guardrail_identifier: str = ""
    bedrock_guardrail_version: str = ""
    retrieval_limit: int = 8
    lexical_candidates: int = 30
    dense_candidates: int = 30
    rrf_k: int = 60
    abstention_min_score: float = 0.015
    answer_max_context_chars: int = 24_000
    worker_id: str = Field(default="local-worker")

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        return Path(self.database_url.removeprefix(prefix))


def embedding_config_values(settings: Settings) -> dict[str, object]:
    return {
        "backend": settings.embedding_backend,
        "model": settings.embedding_model,
        "model_revision": settings.embedding_model_revision,
        "dimensions": settings.embedding_dimensions,
        "distance_metric": settings.embedding_distance,
        "normalize_embeddings": settings.embedding_normalize,
        "pooling": settings.embedding_pooling,
        "document_prefix": settings.embedding_document_prefix,
        "query_prefix": settings.embedding_query_prefix,
        "max_input_tokens": settings.embedding_max_input_tokens,
        "preprocessing_version": settings.embedding_preprocessing_version,
    }


def embedding_config_hash(settings: Settings) -> str:
    serialized = json.dumps(embedding_config_values(settings), sort_keys=True).encode()
    return hashlib.sha256(serialized).hexdigest()


def validate_embedding_revision(settings: Settings) -> None:
    if settings.embedding_backend != "sentence_transformers":
        return
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", settings.embedding_model_revision):
        raise ValueError(
            "embedding_model_revision must be an immutable 40-64 character commit hash"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

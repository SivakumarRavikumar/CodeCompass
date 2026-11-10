import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    availability TEXT NOT NULL,
    indexing_status TEXT NOT NULL,
    active_snapshot_id TEXT,
    pending_snapshot_id TEXT,
    latest_job_id TEXT,
    file_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    degradation TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    embedding_config_id TEXT NOT NULL REFERENCES embedding_configs(id),
    archive_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    active_generation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_configs (
    id TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    distance_metric TEXT NOT NULL,
    normalize_embeddings INTEGER NOT NULL,
    pooling TEXT NOT NULL,
    document_prefix TEXT NOT NULL,
    query_prefix TEXT NOT NULL,
    max_input_tokens INTEGER NOT NULL,
    preprocessing_version TEXT NOT NULL,
    config_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    redacted_content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    UNIQUE(snapshot_id, path)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    symbol TEXT,
    content_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_repo_generation
    ON chunks(repository_id, generation_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, repository_id UNINDEXED, generation_id UNINDEXED,
    path, symbol, content, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    generation_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    lease_until TEXT,
    heartbeat_at TEXT,
    next_attempt_at TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    phase TEXT NOT NULL DEFAULT 'queued',
    stages_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_claim ON jobs(state, lease_until, created_at);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    repository_id TEXT REFERENCES repositories(id) ON DELETE CASCADE,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    abstained INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    question_id TEXT,
    event TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purges (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    lease_until TEXT,
    heartbeat_at TEXT,
    qdrant_deleted_at TEXT,
    sqlite_deleted_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_purges_claim ON purges(status, lease_until, created_at);
CREATE TABLE IF NOT EXISTS vector_generation_cleanups (
    repository_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error TEXT,
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    lease_until TEXT,
    heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(repository_id, generation_id)
);
CREATE INDEX IF NOT EXISTS ix_generation_cleanups_claim
    ON vector_generation_cleanups(status, lease_until, created_at);
"""


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        path = settings.sqlite_path
        if path and path != Path(":memory:"):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(settings.database_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_pragmas(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    await connection.exec_driver_sql(statement)
            columns = (
                await connection.exec_driver_sql("PRAGMA table_info(idempotency_keys)")
            ).mappings()
            if "repository_id" not in {str(column["name"]) for column in columns}:
                await connection.exec_driver_sql(
                    "ALTER TABLE idempotency_keys ADD COLUMN repository_id TEXT"
                )
                rows = (
                    await connection.exec_driver_sql(
                        """
                        SELECT key, response_json FROM idempotency_keys
                        WHERE repository_id IS NULL
                        """
                    )
                ).mappings()
                for row in rows:
                    key = str(row["key"])
                    response_json = row["response_json"]
                    try:
                        repository_id = json.loads(str(response_json))["repository"]["id"]
                    except (KeyError, TypeError, json.JSONDecodeError):
                        continue
                    await connection.exec_driver_sql(
                        """
                        UPDATE idempotency_keys SET repository_id=?
                        WHERE key=? AND repository_id IS NULL
                        """,
                        (repository_id, key),
                    )
            await connection.exec_driver_sql(
                """
                CREATE INDEX IF NOT EXISTS ix_idempotency_repository
                ON idempotency_keys(repository_id)
                """
            )
            repository_columns = (
                await connection.exec_driver_sql("PRAGMA table_info(repositories)")
            ).mappings()
            if "pending_snapshot_id" not in {
                str(column["name"]) for column in repository_columns
            }:
                await connection.exec_driver_sql(
                    "ALTER TABLE repositories ADD COLUMN pending_snapshot_id TEXT"
                )
                await connection.exec_driver_sql(
                    """
                    UPDATE repositories
                    SET pending_snapshot_id=active_snapshot_id,
                        active_snapshot_id=NULL
                    WHERE availability!='available' AND indexing_status!='ready'
                    """
                )

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        async with self.engine.begin() as connection:
            yield connection

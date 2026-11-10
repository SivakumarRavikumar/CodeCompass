import asyncio
import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.adapters import (
    LLM,
    Embedder,
    VectorConfigurationMismatchError,
    VectorStore,
)
from app.archive import SafeFile
from app.chunking import Chunk, chunk_file, expand_search_text
from app.config import Settings, embedding_config_hash, embedding_config_values
from app.database import Database
from app.retrieval import RetrievedChunk, Retriever

logger = structlog.get_logger()


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat()


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


class CancelledError(RuntimeError):
    pass


class VectorGenerationIntegrityError(RuntimeError):
    pass


class EmbeddingConfigurationMismatchError(RuntimeError):
    pass


async def enqueue_generation_cleanup(
    connection: AsyncConnection,
    repository_id: object,
    generation_id: object,
    timestamp: str,
    not_before: str | None = None,
) -> None:
    if not generation_id:
        return
    await connection.execute(
        text(
            """
            INSERT OR IGNORE INTO vector_generation_cleanups(
                repository_id, generation_id, status, next_attempt_at,
                created_at, updated_at
            ) VALUES (:repo, :generation, 'queued', :not_before, :now, :now)
            """
        ),
        {
            "repo": repository_id,
            "generation": generation_id,
            "not_before": not_before,
            "now": timestamp,
        },
    )


def job_status(state: str) -> str:
    return {
        "running": "processing",
        "succeeded": "ready",
        "retry_wait": "queued",
    }.get(state, state)


def map_job(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": row["id"],
        "status": job_status(str(row["state"])),
        "progress": row["progress"],
        "phase": row["phase"],
        "error": row["error"],
        "stages": json.loads(str(row["stages_json"])),
    }


def map_repository(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "availability": row["availability"],
        "indexing_status": row["indexing_status"],
        "snapshot_id": row["active_snapshot_id"],
        "active_snapshot_id": row["active_snapshot_id"],
        "pending_snapshot_id": row["pending_snapshot_id"],
        "job_id": row["latest_job_id"],
        "error": row["error"],
        "file_name": row["file_name"],
        "created_at": row["created_at"],
        "degradation": row["degradation"],
    }


class RepositoryService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def create(
        self,
        display_name: str,
        file_name: str,
        archive_sha: str,
        files: list[SafeFile],
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object]:
        repository_id = str(uuid.uuid4())
        snapshot_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        timestamp = iso()
        async with self.database.transaction() as connection:
            replay = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT i.request_hash, i.response_json,
                                   i.repository_id, r.availability
                            FROM idempotency_keys i
                            LEFT JOIN repositories r ON r.id=i.repository_id
                            WHERE i.key=:key
                            """
                        ),
                        {"key": idempotency_key},
                    )
                )
                .mappings()
                .first()
            )
            if replay:
                if not replay["availability"]:
                    await connection.execute(
                        text("DELETE FROM idempotency_keys WHERE key=:key"),
                        {"key": idempotency_key},
                    )
                    replay = None
                elif replay["availability"] == "deleting":
                    raise ConflictError("idempotent upload target is being deleted")
            if replay:
                if replay["request_hash"] != request_hash:
                    raise ConflictError("idempotency key was already used for another request")
                return json.loads(str(replay["response_json"]))  # type: ignore[no-any-return]

            embedding_config = embedding_config_values(self.settings)
            config_hash = embedding_config_hash(self.settings)
            config_id = str(uuid.uuid5(uuid.NAMESPACE_URL, config_hash))
            await connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO embedding_configs(
                        id, backend, model, model_revision, dimensions, distance_metric,
                        normalize_embeddings, pooling, document_prefix, query_prefix,
                        max_input_tokens, preprocessing_version, config_hash, created_at
                    ) VALUES (
                        :id, :backend, :model, :model_revision, :dimensions, :distance_metric,
                        :normalize_embeddings, :pooling, :document_prefix, :query_prefix,
                        :max_input_tokens, :preprocessing_version, :hash, :now
                    )
                    """
                ),
                {
                    "id": config_id,
                    **embedding_config,
                    "normalize_embeddings": int(self.settings.embedding_normalize),
                    "hash": config_hash,
                    "now": timestamp,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO repositories(
                        id, display_name, availability, indexing_status,
                        active_snapshot_id, pending_snapshot_id, latest_job_id,
                        file_name, created_at
                    ) VALUES (
                        :id, :name, 'unavailable', 'queued', NULL, :snapshot,
                        :job, :file, :now
                    )
                    """
                ),
                {
                    "id": repository_id,
                    "name": display_name,
                    "snapshot": snapshot_id,
                    "job": job_id,
                    "file": file_name,
                    "now": timestamp,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO snapshots(
                        id, repository_id, embedding_config_id,
                        archive_sha256, status, created_at
                    ) VALUES (:id, :repo, :config, :sha, 'queued', :now)
                    """
                ),
                {
                    "id": snapshot_id,
                    "repo": repository_id,
                    "config": config_id,
                    "sha": archive_sha,
                    "now": timestamp,
                },
            )
            if files:
                await connection.execute(
                    text(
                        """
                        INSERT INTO source_files(
                            repository_id, snapshot_id, path, redacted_content, content_sha256
                        ) VALUES (:repo, :snapshot, :path, :content, :digest)
                        """
                    ),
                    [
                        {
                            "repo": repository_id,
                            "snapshot": snapshot_id,
                            "path": item.path,
                            "content": item.content,
                            "digest": hashlib.sha256(item.content.encode()).hexdigest(),
                        }
                        for item in files
                    ],
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO jobs(
                        id, repository_id, snapshot_id, state, created_at, updated_at
                    ) VALUES (:id, :repo, :snapshot, 'queued', :now, :now)
                    """
                ),
                {"id": job_id, "repo": repository_id, "snapshot": snapshot_id, "now": timestamp},
            )
            response: dict[str, object] = {
                "repository": {
                    "id": repository_id,
                    "display_name": display_name,
                    "availability": "unavailable",
                    "indexing_status": "queued",
                    "snapshot_id": None,
                    "active_snapshot_id": None,
                    "pending_snapshot_id": snapshot_id,
                    "job_id": job_id,
                    "error": None,
                    "file_name": file_name,
                    "created_at": timestamp,
                    "degradation": None,
                },
                "snapshot": {"id": snapshot_id},
                "job": {
                    "id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "phase": "queued",
                    "error": None,
                },
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO idempotency_keys(
                        key, repository_id, request_hash, response_json, created_at
                    ) VALUES (:key, :repo, :hash, :response, :now)
                    """
                ),
                {
                    "key": idempotency_key,
                    "repo": repository_id,
                    "hash": request_hash,
                    "response": json.dumps(response),
                    "now": timestamp,
                },
            )
        logger.info(
            "repository_upload_accepted",
            repository_id=repository_id,
            snapshot_id=snapshot_id,
            job_id=job_id,
            file_count=len(files),
        )
        return response

    async def get(self, repository_id: str) -> dict[str, object]:
        async with self.database.session() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM repositories WHERE id=:id"), {"id": repository_id}
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise NotFoundError("repository not found")
        return map_repository(dict(row))

    async def list(self) -> list[dict[str, object]]:
        async with self.database.session() as session:
            rows = (
                await session.execute(text("SELECT * FROM repositories ORDER BY created_at DESC"))
            ).mappings()
            return [map_repository(dict(row)) for row in rows]

    async def get_job(self, job_id: str) -> dict[str, object]:
        async with self.database.session() as session:
            row = (
                (await session.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}))
                .mappings()
                .first()
            )
        if not row:
            raise NotFoundError("job not found")
        return map_job(dict(row))

    async def cancel_job(self, job_id: str) -> dict[str, object]:
        async with self.database.transaction() as connection:
            row = (
                (await connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}))
                .mappings()
                .first()
            )
            if not row:
                raise NotFoundError("job not found")
            if row["state"] not in {"succeeded", "failed", "cancelled"}:
                timestamp = iso()
                await connection.execute(
                    text(
                        """
                        UPDATE jobs SET state='cancelled', phase='cancelled',
                            claim_epoch=claim_epoch+1, lease_until=NULL, updated_at=:now
                        WHERE id=:id
                        """
                    ),
                    {"id": job_id, "now": timestamp},
                )
                await enqueue_generation_cleanup(
                    connection,
                    row["repository_id"],
                    row["generation_id"],
                    timestamp,
                    (
                        iso(now() + timedelta(seconds=self.settings.worker_lease_seconds))
                        if row["state"] == "running"
                        else None
                    ),
                )
                await connection.execute(
                    text(
                        """
                        UPDATE repositories SET indexing_status='cancelled',
                            pending_snapshot_id=NULL, error='indexing cancelled'
                        WHERE id=:repo
                        """
                    ),
                    {"repo": row["repository_id"]},
                )
                await connection.execute(
                    text("UPDATE snapshots SET status='cancelled' WHERE id=:snapshot"),
                    {"snapshot": row["snapshot_id"]},
                )
            return await self._job_in_connection(connection, job_id)

    async def _job_in_connection(
        self, connection: AsyncConnection, job_id: str
    ) -> dict[str, object]:
        row = (
            (await connection.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}))
            .mappings()
            .one()
        )
        return map_job(dict(row))

    async def tombstone(self, repository_id: str) -> dict[str, object]:
        timestamp = iso()
        async with self.database.transaction() as connection:
            existing = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT * FROM purges WHERE repository_id=:id
                        ORDER BY created_at DESC LIMIT 1
                        """
                        ),
                        {"id": repository_id},
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return self._map_purge(dict(existing))
            repository = await connection.scalar(
                text("SELECT 1 FROM repositories WHERE id=:id"), {"id": repository_id}
            )
            if not repository:
                raise NotFoundError("repository not found")
            purge_id = str(uuid.uuid4())
            running_job = await connection.scalar(
                text(
                    """
                    SELECT 1 FROM jobs
                    WHERE repository_id=:id AND state='running'
                    LIMIT 1
                    """
                ),
                {"id": repository_id},
            )
            purge_not_before = (
                iso(now() + timedelta(seconds=self.settings.worker_lease_seconds))
                if running_job
                else None
            )
            await connection.execute(
                text(
                    """
                    UPDATE repositories SET availability='deleting', deleted_at=:now
                    WHERE id=:id
                    """
                ),
                {"id": repository_id, "now": timestamp},
            )
            await connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO vector_generation_cleanups(
                        repository_id, generation_id, status, next_attempt_at,
                        created_at, updated_at
                    )
                    SELECT repository_id, generation_id, 'queued', :not_before, :now, :now
                    FROM jobs
                    WHERE repository_id=:id AND generation_id IS NOT NULL
                      AND state NOT IN ('succeeded','failed','cancelled')
                    """
                ),
                {
                    "id": repository_id,
                    "not_before": purge_not_before,
                    "now": timestamp,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE jobs SET state='cancelled', phase='cancelled',
                        claim_epoch=claim_epoch+1, lease_until=NULL, updated_at=:now
                    WHERE repository_id=:id AND state NOT IN ('succeeded','failed','cancelled')
                    """
                ),
                {"id": repository_id, "now": timestamp},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO purges(
                        id, repository_id, status, next_attempt_at, created_at, updated_at
                    ) VALUES (:id, :repo, 'queued', :not_before, :now, :now)
                    """
                ),
                {
                    "id": purge_id,
                    "repo": repository_id,
                    "not_before": purge_not_before,
                    "now": timestamp,
                },
            )
            return {
                "id": purge_id,
                "status": "queued",
                "progress": 0,
                "error": None,
            }

    @staticmethod
    def _map_purge(row: dict[str, object]) -> dict[str, object]:
        return {
            "id": row["id"],
            "status": "queued" if row["status"] == "retry_wait" else row["status"],
            "progress": row["progress"],
            "error": row["error"],
        }

    async def get_purge(self, purge_id: str) -> dict[str, object]:
        async with self.database.session() as session:
            row = (
                (await session.execute(text("SELECT * FROM purges WHERE id=:id"), {"id": purge_id}))
                .mappings()
                .first()
            )
        if not row:
            raise NotFoundError("purge not found")
        return self._map_purge(dict(row))


class DurableWorker:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        embedder: Embedder,
        vectors: VectorStore,
    ) -> None:
        self.database = database
        self.settings = settings
        self.embedder = embedder
        self.vectors = vectors
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            worked = await self.run_once()
            if not worked:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.worker_poll_seconds
                    )
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stop.set()

    async def _claim_job(self) -> dict[str, object] | None:
        timestamp = iso()
        lease = iso(now() + timedelta(seconds=self.settings.worker_lease_seconds))
        generation = str(uuid.uuid4())
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        UPDATE jobs SET state='running', attempts=attempts+1,
                            claim_epoch=claim_epoch+1, claimed_by=:worker,
                            generation_id=:generation, lease_until=:lease,
                            heartbeat_at=:now, phase='extracting', progress=5, updated_at=:now
                        WHERE id=(
                            SELECT id FROM jobs
                            WHERE state='queued'
                               OR (state='retry_wait' AND next_attempt_at <= :now)
                               OR (state='running' AND lease_until < :now)
                            ORDER BY created_at LIMIT 1
                        )
                        RETURNING *
                        """
                        ),
                        {
                            "worker": self.settings.worker_id,
                            "generation": generation,
                            "lease": lease,
                            "now": timestamp,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row:
                await connection.execute(
                    text(
                        """
                        UPDATE repositories SET indexing_status='processing', error=NULL
                        WHERE id=:repo AND latest_job_id=:job
                        """
                    ),
                    {"repo": row["repository_id"], "job": row["id"]},
                )
                await connection.execute(
                    text("UPDATE snapshots SET status='indexing' WHERE id=:snapshot"),
                    {"snapshot": row["snapshot_id"]},
                )
        return dict(row) if row else None

    async def _claim_purge(self) -> dict[str, object] | None:
        timestamp = iso()
        lease = iso(now() + timedelta(seconds=self.settings.worker_lease_seconds))
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        UPDATE purges SET status='processing', progress=10,
                            attempts=attempts+1,
                            claim_epoch=claim_epoch+1, claimed_by=:worker,
                            lease_until=:lease, heartbeat_at=:now, updated_at=:now
                        WHERE id=(
                            SELECT id FROM purges
                            WHERE (status='queued'
                                   AND (next_attempt_at IS NULL OR next_attempt_at <= :now))
                               OR (status='retry_wait' AND next_attempt_at <= :now)
                               OR (status='processing' AND lease_until < :now)
                            ORDER BY created_at LIMIT 1
                        )
                        RETURNING *
                        """
                        ),
                        {"worker": self.settings.worker_id, "lease": lease, "now": timestamp},
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def _claim_generation_cleanup(self) -> dict[str, object] | None:
        timestamp = iso()
        lease = iso(now() + timedelta(seconds=self.settings.worker_lease_seconds))
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            UPDATE vector_generation_cleanups
                            SET status='processing', attempts=attempts+1,
                                claim_epoch=claim_epoch+1, claimed_by=:worker,
                                lease_until=:lease, heartbeat_at=:now, updated_at=:now
                            WHERE (repository_id, generation_id)=(
                                SELECT repository_id, generation_id
                                FROM vector_generation_cleanups
                                WHERE (status='queued'
                                       AND (next_attempt_at IS NULL OR next_attempt_at <= :now))
                                   OR (status='retry_wait' AND next_attempt_at <= :now)
                                   OR (status='processing' AND lease_until < :now)
                                ORDER BY created_at LIMIT 1
                            )
                            RETURNING *
                            """
                        ),
                        {
                            "worker": self.settings.worker_id,
                            "lease": lease,
                            "now": timestamp,
                        },
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def run_once(self) -> bool:
        job = await self._claim_job()
        if job:
            try:
                await self._ingest(job)
            except CancelledError:
                pass
            except Exception as exc:
                await self._retry_or_fail(job, exc)
            return True
        purge = await self._claim_purge()
        if purge:
            await self._purge(purge)
            return True
        cleanup = await self._claim_generation_cleanup()
        if cleanup:
            await self._cleanup_generation(cleanup)
            return True
        return False

    async def _heartbeat(self, job: dict[str, object], phase: str, progress: int) -> None:
        async with self.database.transaction() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE jobs SET heartbeat_at=:now, lease_until=:lease,
                        phase=:phase, progress=:progress, updated_at=:now
                    WHERE id=:id AND state='running' AND claim_epoch=:epoch
                      AND claimed_by=:worker
                    """
                ),
                {
                    "id": job["id"],
                    "epoch": job["claim_epoch"],
                    "worker": self.settings.worker_id,
                    "now": iso(),
                    "lease": iso(now() + timedelta(seconds=self.settings.worker_lease_seconds)),
                    "phase": phase,
                    "progress": progress,
                },
            )
            if result.rowcount != 1:
                raise CancelledError("job claim was cancelled or superseded")

    async def _heartbeat_purge(self, purge: dict[str, object], progress: int) -> None:
        async with self.database.transaction() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE purges SET heartbeat_at=:now, lease_until=:lease,
                        progress=:progress, updated_at=:now
                    WHERE id=:id AND status='processing' AND claim_epoch=:epoch
                      AND claimed_by=:worker
                    """
                ),
                {
                    "id": purge["id"],
                    "epoch": purge["claim_epoch"],
                    "worker": self.settings.worker_id,
                    "now": iso(),
                    "lease": iso(now() + timedelta(seconds=self.settings.worker_lease_seconds)),
                    "progress": progress,
                },
            )
            if result.rowcount != 1:
                raise CancelledError("purge claim was cancelled or superseded")

    async def _renew_purge_lease(
        self,
        purge: dict[str, object],
        stop: asyncio.Event,
        failures: list[Exception],
    ) -> None:
        interval = max(0.1, self.settings.worker_lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                try:
                    await self._heartbeat_purge(purge, 30)
                except Exception as exc:
                    failures.append(exc)
                    stop.set()

    async def _heartbeat_generation_cleanup(self, cleanup: dict[str, object]) -> None:
        async with self.database.transaction() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE vector_generation_cleanups
                    SET heartbeat_at=:now, lease_until=:lease, updated_at=:now
                    WHERE repository_id=:repo AND generation_id=:generation
                      AND status='processing' AND claim_epoch=:epoch
                      AND claimed_by=:worker
                    """
                ),
                {
                    "repo": cleanup["repository_id"],
                    "generation": cleanup["generation_id"],
                    "epoch": cleanup["claim_epoch"],
                    "worker": self.settings.worker_id,
                    "now": iso(),
                    "lease": iso(now() + timedelta(seconds=self.settings.worker_lease_seconds)),
                },
            )
            if result.rowcount != 1:
                raise CancelledError("generation cleanup claim was superseded")

    async def _ingest(self, job: dict[str, object]) -> None:
        started = time.monotonic()
        repository_id = str(job["repository_id"])
        snapshot_id = str(job["snapshot_id"])
        generation_id = str(job["generation_id"])
        async with self.database.session() as session:
            stored_config_hash = await session.scalar(
                text(
                    """
                    SELECT ec.config_hash
                    FROM snapshots s
                    JOIN embedding_configs ec ON ec.id=s.embedding_config_id
                    WHERE s.id=:snapshot
                    """
                ),
                {"snapshot": snapshot_id},
            )
            if stored_config_hash != embedding_config_hash(self.settings):
                raise EmbeddingConfigurationMismatchError(
                    "snapshot embedding configuration does not match runtime configuration"
                )
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT path, redacted_content FROM source_files
                        WHERE snapshot_id=:snapshot ORDER BY path
                        """
                    ),
                    {"snapshot": snapshot_id},
                )
            ).all()
        chunks: list[Chunk] = []
        for path, content in rows:
            chunks.extend(
                chunk_file(repository_id, snapshot_id, str(path), str(content), self.settings)
            )
        await self._heartbeat(job, "embedding", 35)
        degradation = None
        try:
            await self.vectors.ensure()
            for offset in range(0, len(chunks), 64):
                batch = chunks[offset : offset + 64]
                vectors = await self.embedder.embed(
                    [
                        f"{self.settings.embedding_document_prefix}{chunk.content}"
                        for chunk in batch
                    ]
                )
                await self.vectors.upsert(repository_id, generation_id, batch, vectors)
                await self._heartbeat(job, "embedding", min(70, 35 + offset))
            expected_ids = {chunk.id for chunk in chunks}
            if not await self.vectors.verify(repository_id, generation_id, expected_ids):
                raise VectorGenerationIntegrityError(
                    "vector generation ID verification failed"
                )
        except CancelledError:
            raise
        except (
            VectorConfigurationMismatchError,
            VectorGenerationIntegrityError,
        ):
            raise
        except Exception as exc:
            degradation = f"lexical-only: {type(exc).__name__}"
            logger.warning(
                "dense_index_degraded",
                repository_id=repository_id,
                generation_id=generation_id,
                reason=type(exc).__name__,
            )
        await self._heartbeat(job, "activating", 85)
        values = [
            {
                "id": chunk.id,
                "repo": repository_id,
                "snapshot": snapshot_id,
                "generation": generation_id,
                "path": chunk.path,
                "start": chunk.start_line,
                "end": chunk.end_line,
                "content": chunk.content,
                "search_content": expand_search_text(
                    " ".join(
                        value
                        for value in (chunk.path, chunk.symbol, chunk.content)
                        if value
                    )
                ),
                "symbol": chunk.symbol,
                "digest": chunk.content_sha256,
            }
            for chunk in chunks
        ]
        stages = [
            {"name": "extract", "detail": f"{len(rows)} files"},
            {"name": "chunk", "detail": f"{len(chunks)} chunks"},
            {"name": "activate", "duration_ms": int((time.monotonic() - started) * 1000)},
        ]
        async with self.database.transaction() as connection:
            owner = await connection.scalar(
                text(
                    """
                    SELECT 1 FROM jobs WHERE id=:id AND state='running'
                      AND claim_epoch=:epoch AND claimed_by=:worker
                    """
                ),
                {
                    "id": job["id"],
                    "epoch": job["claim_epoch"],
                    "worker": self.settings.worker_id,
                },
            )
            if not owner:
                raise CancelledError("job claim was cancelled or superseded")
            if values:
                await connection.execute(
                    text(
                        """
                        INSERT INTO chunks(
                            id, repository_id, snapshot_id, generation_id, path,
                            start_line, end_line, content, symbol, content_sha256
                        ) VALUES (
                            :id, :repo, :snapshot, :generation, :path,
                            :start, :end, :content, :symbol, :digest
                        )
                        """
                    ),
                    values,
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO chunks_fts(
                            chunk_id, repository_id, generation_id, path, symbol, content
                        ) VALUES (:id, :repo, :generation, :path, :symbol, :search_content)
                        """
                    ),
                    values,
                )
            await connection.execute(
                text(
                    """
                    UPDATE snapshots SET status='ready', active_generation_id=:generation
                    WHERE id=:snapshot
                    """
                ),
                {"snapshot": snapshot_id, "generation": generation_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE repositories SET availability='available',
                        indexing_status='ready', active_snapshot_id=:snapshot,
                        pending_snapshot_id=NULL, degradation=:degradation,
                        error=NULL WHERE id=:repo
                    """
                ),
                {
                    "repo": repository_id,
                    "snapshot": snapshot_id,
                    "degradation": degradation,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE jobs SET state='succeeded', phase='ready', progress=100,
                        stages_json=:stages, lease_until=NULL, updated_at=:now
                    WHERE id=:id AND claim_epoch=:epoch
                    """
                ),
                {
                    "id": job["id"],
                    "epoch": job["claim_epoch"],
                    "stages": json.dumps(stages),
                    "now": iso(),
                },
            )

    async def _retry_or_fail(self, job: dict[str, object], exc: Exception) -> None:
        attempts = int(str(job["attempts"]))
        retry = attempts < self.settings.worker_max_attempts and not isinstance(
            exc,
            (
                EmbeddingConfigurationMismatchError,
                VectorConfigurationMismatchError,
            ),
        )
        state = "retry_wait" if retry else "failed"
        error = f"{type(exc).__name__}: {str(exc)[:250]}"
        delay = self.settings.worker_retry_base_seconds * (2 ** max(attempts - 1, 0))
        timestamp = iso()
        async with self.database.transaction() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE jobs SET state=:state, phase=:phase, error=:error,
                        next_attempt_at=:next, lease_until=NULL, updated_at=:now
                    WHERE id=:id AND state='running' AND claim_epoch=:epoch
                    """
                ),
                {
                    "state": state,
                    "phase": state,
                    "error": error,
                    "next": iso(now() + timedelta(seconds=delay)) if retry else None,
                    "now": timestamp,
                    "id": job["id"],
                    "epoch": job["claim_epoch"],
                },
            )
            if result.rowcount:
                await enqueue_generation_cleanup(
                    connection,
                    job["repository_id"],
                    job["generation_id"],
                    timestamp,
                )
            if result.rowcount:
                await connection.execute(
                    text(
                        """
                        UPDATE repositories SET indexing_status=:status,
                            pending_snapshot_id=:pending_snapshot, error=:error
                        WHERE id=:repo
                        """
                    ),
                    {
                        "repo": job["repository_id"],
                        "status": "queued" if retry else "failed",
                        "pending_snapshot": job["snapshot_id"] if retry else None,
                        "error": error,
                    },
                )
                await connection.execute(
                    text("UPDATE snapshots SET status=:status WHERE id=:snapshot"),
                    {
                        "snapshot": job["snapshot_id"],
                        "status": "queued" if retry else "failed",
                    },
                )

    async def _purge(self, purge: dict[str, object]) -> None:
        repository_id = str(purge["repository_id"])
        try:
            await self._heartbeat_purge(purge, 20)
            stop_renewal = asyncio.Event()
            renewal_failures: list[Exception] = []
            renewal_task = asyncio.create_task(
                self._renew_purge_lease(purge, stop_renewal, renewal_failures)
            )
            try:
                verified = await self.vectors.delete_repository(repository_id)
            finally:
                stop_renewal.set()
                await renewal_task
            if renewal_failures:
                raise renewal_failures[0]
            if not verified:
                raise RuntimeError("vector deletion verification failed")
            await self._heartbeat_purge(purge, 50)
            async with self.database.transaction() as connection:
                owner = await connection.scalar(
                    text(
                        """
                        SELECT 1 FROM purges WHERE id=:id AND status='processing'
                          AND claim_epoch=:epoch AND claimed_by=:worker
                        """
                    ),
                    {
                        "id": purge["id"],
                        "epoch": purge["claim_epoch"],
                        "worker": self.settings.worker_id,
                    },
                )
                if not owner:
                    return
                await connection.execute(
                    text("DELETE FROM chunks_fts WHERE repository_id=:repo"),
                    {"repo": repository_id},
                )
                await connection.execute(
                    text("DELETE FROM idempotency_keys WHERE repository_id=:repo"),
                    {"repo": repository_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE purges SET progress=60, qdrant_deleted_at=:now
                        WHERE id=:id AND claim_epoch=:epoch
                        """
                    ),
                    {
                        "id": purge["id"],
                        "epoch": purge["claim_epoch"],
                        "now": iso(),
                    },
                )
                await connection.execute(
                    text("DELETE FROM repositories WHERE id=:repo"),
                    {"repo": repository_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE purges SET status='completed', progress=100,
                            sqlite_deleted_at=:now, completed_at=:now,
                            lease_until=NULL, updated_at=:now
                        WHERE id=:id AND status='processing'
                          AND claim_epoch=:epoch AND claimed_by=:worker
                        """
                    ),
                    {
                        "id": purge["id"],
                        "epoch": purge["claim_epoch"],
                        "worker": self.settings.worker_id,
                        "now": iso(),
                    },
                )
        except Exception as exc:
            attempts = int(str(purge["attempts"]))
            retry = attempts < self.settings.worker_max_attempts
            retry_at = (
                iso(
                    now()
                    + timedelta(
                        seconds=self.settings.worker_retry_base_seconds
                        * (2 ** max(attempts - 1, 0))
                    )
                )
                if retry
                else None
            )
            async with self.database.transaction() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE purges SET status=:status, error=:error,
                            next_attempt_at=:next_attempt_at,
                            lease_until=NULL, updated_at=:now
                        WHERE id=:id AND claim_epoch=:epoch
                        """
                    ),
                    {
                        "id": purge["id"],
                        "epoch": purge["claim_epoch"],
                        "status": "retry_wait" if retry else "failed",
                        "next_attempt_at": retry_at,
                        "error": f"{type(exc).__name__}: {str(exc)[:250]}",
                        "now": iso(),
                    },
                )

    async def _cleanup_generation(self, cleanup: dict[str, object]) -> None:
        repository_id = str(cleanup["repository_id"])
        generation_id = str(cleanup["generation_id"])
        try:
            await self._heartbeat_generation_cleanup(cleanup)
            async with self.database.session() as session:
                active = await session.scalar(
                    text(
                        """
                        SELECT 1 FROM snapshots
                        WHERE repository_id=:repo AND active_generation_id=:generation
                        LIMIT 1
                        """
                    ),
                    {"repo": repository_id, "generation": generation_id},
                )
                live_worker = await session.scalar(
                    text(
                        """
                        SELECT 1 FROM jobs
                        WHERE repository_id=:repo AND generation_id=:generation
                          AND state='running' AND lease_until >= :now
                        LIMIT 1
                        """
                    ),
                    {
                        "repo": repository_id,
                        "generation": generation_id,
                        "now": iso(),
                    },
                )
            if active:
                async with self.database.transaction() as connection:
                    await connection.execute(
                        text(
                            """
                            UPDATE vector_generation_cleanups
                            SET status='protected', error='generation is active',
                                lease_until=NULL, updated_at=:now
                            WHERE repository_id=:repo AND generation_id=:generation
                              AND status='processing' AND claim_epoch=:epoch
                              AND claimed_by=:worker
                            """
                        ),
                        {
                            "repo": repository_id,
                            "generation": generation_id,
                            "epoch": cleanup["claim_epoch"],
                            "worker": self.settings.worker_id,
                            "now": iso(),
                        },
                    )
                return
            if live_worker:
                raise RuntimeError("generation is still owned by a live worker")
            deleted = await self.vectors.delete_generation(repository_id, generation_id)
            if not deleted:
                raise RuntimeError("vector generation deletion verification failed")
            await self._heartbeat_generation_cleanup(cleanup)
            async with self.database.transaction() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE vector_generation_cleanups
                        SET status='completed', completed_at=:now,
                            lease_until=NULL, updated_at=:now, error=NULL
                        WHERE repository_id=:repo AND generation_id=:generation
                          AND status='processing' AND claim_epoch=:epoch
                          AND claimed_by=:worker
                        """
                    ),
                    {
                        "repo": repository_id,
                        "generation": generation_id,
                        "epoch": cleanup["claim_epoch"],
                        "worker": self.settings.worker_id,
                        "now": iso(),
                    },
                )
        except Exception as exc:
            attempts = int(str(cleanup["attempts"]))
            retry = attempts < self.settings.worker_max_attempts
            retry_at = (
                iso(
                    now()
                    + timedelta(
                        seconds=self.settings.worker_retry_base_seconds
                        * (2 ** max(attempts - 1, 0))
                    )
                )
                if retry
                else None
            )
            async with self.database.transaction() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE vector_generation_cleanups
                        SET status=:status, error=:error,
                            next_attempt_at=:next_attempt_at,
                            lease_until=NULL, updated_at=:now
                        WHERE repository_id=:repo AND generation_id=:generation
                          AND status='processing' AND claim_epoch=:epoch
                          AND claimed_by=:worker
                        """
                    ),
                    {
                        "repo": repository_id,
                        "generation": generation_id,
                        "epoch": cleanup["claim_epoch"],
                        "worker": self.settings.worker_id,
                        "status": "retry_wait" if retry else "failed",
                        "next_attempt_at": retry_at,
                        "error": f"{type(exc).__name__}: {str(exc)[:250]}",
                        "now": iso(),
                    },
                )

    async def reconcile(self) -> tuple[int, int]:
        timestamp = iso()
        cleanup_after = iso(now() + timedelta(seconds=self.settings.worker_lease_seconds))
        async with self.database.transaction() as connection:
            await connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO vector_generation_cleanups(
                        repository_id, generation_id, status, next_attempt_at,
                        created_at, updated_at
                    )
                    SELECT j.repository_id, j.generation_id, 'queued', :cleanup_after,
                           :now, :now
                    FROM jobs j
                    WHERE j.generation_id IS NOT NULL
                      AND (
                          j.state IN ('failed', 'cancelled')
                          OR (j.state='running' AND j.lease_until < :now)
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM snapshots s
                          WHERE s.repository_id=j.repository_id
                            AND s.active_generation_id=j.generation_id
                      )
                    """
                ),
                {"now": timestamp, "cleanup_after": cleanup_after},
            )
            await connection.execute(
                text(
                    """
                    UPDATE repositories
                    SET indexing_status='queued',
                        pending_snapshot_id=(
                            SELECT snapshot_id FROM jobs
                            WHERE jobs.id=repositories.latest_job_id
                        )
                    WHERE latest_job_id IN (
                        SELECT id FROM jobs
                        WHERE state='running' AND lease_until < :now
                    )
                    """
                ),
                {"now": timestamp},
            )
            await connection.execute(
                text(
                    """
                    UPDATE snapshots SET status='queued'
                    WHERE id IN (
                        SELECT snapshot_id FROM jobs
                        WHERE state='running' AND lease_until < :now
                    )
                    """
                ),
                {"now": timestamp},
            )
            jobs = await connection.execute(
                text(
                    """
                    UPDATE jobs SET state='queued', claimed_by=NULL, lease_until=NULL,
                        phase='queued', updated_at=:now
                    WHERE state='running' AND lease_until < :now
                    """
                ),
                {"now": timestamp},
            )
            purges = await connection.execute(
                text(
                    """
                    UPDATE purges SET status='queued', claimed_by=NULL, lease_until=NULL,
                        updated_at=:now
                    WHERE status='processing' AND lease_until < :now
                    """
                ),
                {"now": timestamp},
            )
            await connection.execute(
                text(
                    """
                    UPDATE vector_generation_cleanups
                    SET status='queued', claimed_by=NULL, lease_until=NULL, updated_at=:now
                    WHERE status='processing' AND lease_until < :now
                    """
                ),
                {"now": timestamp},
            )
        return jobs.rowcount, purges.rowcount


class QuestionService:
    CITATION = re.compile(r"\[C(\d+)]")
    ANY_CITATION = re.compile(r"\[C[^\]]*]")

    def __init__(
        self,
        database: Database,
        settings: Settings,
        retriever: Retriever,
        llm: LLM,
    ) -> None:
        self.database = database
        self.settings = settings
        self.retriever = retriever
        self.llm = llm

    @classmethod
    def _validated_citation_labels(
        cls, generated: str, citation_count: int
    ) -> list[int] | None:
        raw_markers = cls.ANY_CITATION.findall(generated)
        numeric_matches = list(cls.CITATION.finditer(generated))
        if not raw_markers or len(raw_markers) != len(numeric_matches):
            return None
        raw_labels = [int(match.group(1)) for match in numeric_matches]
        if any(label < 1 or label > citation_count for label in raw_labels):
            return None
        if any(
            match.group(0) != f"[C{label}]"
            for match, label in zip(numeric_matches, raw_labels, strict=True)
        ):
            return None
        paragraphs = [
            paragraph.strip() for paragraph in generated.split("\n\n") if paragraph.strip()
        ]
        if not paragraphs or not all(cls.CITATION.search(paragraph) for paragraph in paragraphs):
            return None
        return sorted(set(raw_labels))

    @classmethod
    def _drop_uncited_paragraphs(cls, generated: str) -> str:
        """Keep paragraphs that carry a citation label and discard the rest."""
        paragraphs = [
            paragraph.strip() for paragraph in generated.split("\n\n") if paragraph.strip()
        ]
        cited = [paragraph for paragraph in paragraphs if cls.CITATION.search(paragraph)]
        return "\n\n".join(cited)

    @staticmethod
    def _pack_contexts(
        chunks: list[RetrievedChunk], character_budget: int
    ) -> tuple[list[RetrievedChunk], list[str]]:
        packed_chunks: list[RetrievedChunk] = []
        contexts: list[str] = []
        used = 0
        for chunk in chunks:
            context = f"{chunk.path}:{chunk.start_line}-{chunk.end_line}\n{chunk.content}"
            labeled_length = len(f"[C{len(contexts) + 1}] {context}")
            required = labeled_length + (2 if contexts else 0)
            if used + required > character_budget:
                break
            packed_chunks.append(chunk)
            contexts.append(context)
            used += required
        return packed_chunks, contexts

    async def answer(self, repository_id: str, question: str) -> dict[str, object]:
        started = time.monotonic()
        async with self.database.session() as session:
            repository = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT r.availability, r.degradation, s.active_generation_id,
                               ec.config_hash
                        FROM repositories r
                        JOIN snapshots s ON s.id=r.active_snapshot_id
                        JOIN embedding_configs ec ON ec.id=s.embedding_config_id
                        WHERE r.id=:id
                        """
                        ),
                        {"id": repository_id},
                    )
                )
                .mappings()
                .first()
            )
        if not repository:
            raise NotFoundError("repository not found")
        if repository["availability"] != "available" or not repository["active_generation_id"]:
            raise ConflictError("repository is not available")
        if repository["config_hash"] != embedding_config_hash(self.settings):
            raise ConflictError(
                "embedding configuration mismatch; repository re-indexing is required"
            )
        retrieval = await self.retriever.retrieve(
            repository_id,
            str(repository["active_generation_id"]),
            question,
            allow_dense=not bool(repository["degradation"]),
        )
        selected = retrieval.chunks
        abstained = not selected or retrieval.top_score < self.settings.abstention_min_score
        citations: list[dict[str, object]] = []
        answer: str | None = None
        message: str | None = None
        if abstained:
            message = "No relevant source was found."
        else:
            answer_chunks, contexts = self._pack_contexts(
                selected, self.settings.answer_max_context_chars
            )
            if not contexts:
                abstained = True
                message = (
                    "Retrieved evidence could not fit within the configured answer "
                    "context budget."
                )
            else:
                candidate = ""
                valid_labels: list[int] = []
                for _ in range(2):
                    generated = await self.llm.answer(question, contexts)
                    grounded = self._drop_uncited_paragraphs(generated)
                    labels = self._validated_citation_labels(
                        grounded, len(answer_chunks)
                    )
                    if labels:
                        candidate = grounded
                        valid_labels = labels
                        break
                if not valid_labels:
                    abstained = True
                    message = "The generated answer could not be tied to verified evidence."
                else:
                    answer = candidate
                    for label in valid_labels:
                        chunk = answer_chunks[label - 1]
                        citations.append(
                            {
                                "id": chunk.id,
                                "path": chunk.path,
                                "start_line": chunk.start_line,
                                "end_line": chunk.end_line,
                                "snippet": chunk.content[:500],
                                "redacted": True,
                            }
                        )
        trace_id = str(uuid.uuid4())
        question_id = str(uuid.uuid4())
        duration = int((time.monotonic() - started) * 1000)
        trace = {
            "id": trace_id,
            "query": question,
            "duration_ms": duration,
            "candidates": len(selected),
            "retrieved": [
                {
                    "path": chunk.path,
                    "score": chunk.score,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                }
                for chunk in selected
            ],
            "stages": [
                {"name": "retrieve", "detail": retrieval.mode},
                {
                    "name": "degradation",
                    "detail": retrieval.degradation or "none",
                },
            ],
        }
        outcome = "insufficient_evidence" if abstained else "answered"
        async with self.database.transaction() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO questions(
                        id, repository_id, question, answer, citations_json,
                        retrieval_mode, abstained, created_at
                    ) VALUES (:id, :repo, :question, :answer, :citations, :mode, :abstained, :now)
                    """
                ),
                {
                    "id": question_id,
                    "repo": repository_id,
                    "question": question,
                    "answer": answer or message or "",
                    "citations": json.dumps(citations),
                    "mode": retrieval.mode,
                    "abstained": int(abstained),
                    "now": iso(),
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO traces(id, repository_id, question_id, event, data_json, created_at)
                    VALUES (:id, :repo, :question, 'question_answered', :data, :now)
                    """
                ),
                {
                    "id": trace_id,
                    "repo": repository_id,
                    "question": question_id,
                    "data": json.dumps(trace),
                    "now": iso(),
                },
            )
        return {
            "outcome": outcome,
            "answer": answer,
            "message": message,
            "citations": citations,
            "trace_id": trace_id,
        }

    async def trace(self, trace_id: str) -> dict[str, object]:
        async with self.database.session() as session:
            row = (
                await session.execute(
                    text("SELECT data_json FROM traces WHERE id=:id"), {"id": trace_id}
                )
            ).first()
        if not row:
            raise NotFoundError("trace not found")
        return json.loads(str(row[0]))  # type: ignore[no-any-return]

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import PurePath
from typing import Annotated, cast

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters import LLMUnavailableError, build_adapters
from app.archive import UnsafeArchive, read_safe_zip
from app.config import Settings, get_settings
from app.database import Database
from app.logging import configure_logging
from app.retrieval import Retriever
from app.schemas import (
    AnswerResponse,
    JobResponse,
    PurgeResponse,
    QuestionRequest,
    ReconcileResponse,
    RepositoryResponse,
    TraceResponse,
    UploadResponse,
)
from app.services import (
    ConflictError,
    DurableWorker,
    NotFoundError,
    QuestionService,
    RepositoryService,
)

API = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved)
        await database.initialize()
        embedder, vectors, llm = build_adapters(resolved)
        repositories = RepositoryService(database, resolved)
        questions = QuestionService(
            database, resolved, Retriever(database, resolved, embedder, vectors), llm
        )
        worker = DurableWorker(database, resolved, embedder, vectors)
        app.state.database = database
        app.state.repositories = repositories
        app.state.questions = questions
        app.state.worker = worker
        await worker.reconcile()
        task = (
            asyncio.create_task(worker.run(), name="codecompass-worker")
            if resolved.background_worker_enabled
            else None
        )
        yield
        worker.stop()
        if task:
            with suppress(asyncio.CancelledError):
                await task
        await database.close()

    app = FastAPI(title="CodeCompass API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def repositories() -> RepositoryService:
        return cast(RepositoryService, app.state.repositories)

    def questions() -> QuestionService:
        return cast(QuestionService, app.state.questions)

    def worker() -> DurableWorker:
        return cast(DurableWorker, app.state.worker)

    @app.exception_handler(NotFoundError)
    async def not_found(_: object, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "not_found"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(_: object, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "conflict"}, status_code=409)

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable(_: object, _exc: LLMUnavailableError) -> JSONResponse:
        return JSONResponse(
            {
                "detail": "local answer model is unavailable",
                "code": "model_unavailable",
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: object, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.detail, "code": f"http_{exc.status_code}"},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.get(f"{API}/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        f"{API}/repositories",
        response_model=UploadResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def upload_repository(
        display_name: Annotated[str, Form(min_length=1, max_length=200)],
        file: Annotated[UploadFile, File()],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    ) -> dict[str, object]:
        file_name = PurePath(file.filename or "repository.zip").name
        if not file_name.lower().endswith(".zip"):
            raise HTTPException(415, "only .zip uploads are accepted")
        if file.content_type not in {
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        }:
            raise HTTPException(415, "only ZIP uploads are accepted")
        payload = await file.read(resolved.upload_max_bytes + 1)
        if len(payload) > resolved.upload_max_bytes:
            raise HTTPException(413, "upload exceeds configured limit")
        archive_sha = hashlib.sha256(payload).hexdigest()
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "display_name": display_name,
                    "file_name": file_name,
                    "archive_sha": archive_sha,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        try:
            safe_files = read_safe_zip(payload, resolved)
        except UnsafeArchive as exc:
            raise HTTPException(400, str(exc)) from exc
        if not safe_files:
            raise HTTPException(400, "archive contains no UTF-8 source files")
        return await repositories().create(
            display_name,
            file_name,
            archive_sha,
            safe_files,
            idempotency_key,
            request_hash,
        )

    @app.get(f"{API}/repositories", response_model=list[RepositoryResponse])
    async def list_repositories() -> list[dict[str, object]]:
        return await repositories().list()

    @app.get(f"{API}/repositories/{{repository_id}}", response_model=RepositoryResponse)
    async def get_repository(repository_id: str) -> dict[str, object]:
        return await repositories().get(repository_id)

    @app.get(f"{API}/jobs/{{job_id}}", response_model=JobResponse)
    async def get_job(job_id: str) -> dict[str, object]:
        return await repositories().get_job(job_id)

    @app.post(f"{API}/jobs/{{job_id}}/cancel", response_model=JobResponse)
    async def cancel_job(job_id: str) -> dict[str, object]:
        return await repositories().cancel_job(job_id)

    @app.post(
        f"{API}/repositories/{{repository_id}}/questions",
        response_model=AnswerResponse,
        responses={
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "description": "The configured local answer model is unavailable."
            }
        },
    )
    async def ask(repository_id: str, request: QuestionRequest) -> dict[str, object]:
        return await questions().answer(repository_id, request.question)

    @app.get(f"{API}/traces/{{trace_id}}", response_model=TraceResponse)
    async def get_trace(trace_id: str, response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return await questions().trace(trace_id)

    @app.delete(
        f"{API}/repositories/{{repository_id}}",
        response_model=PurgeResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def delete_repository(repository_id: str) -> dict[str, object]:
        return await repositories().tombstone(repository_id)

    @app.get(f"{API}/purges/{{purge_id}}", response_model=PurgeResponse)
    async def get_purge(purge_id: str) -> dict[str, object]:
        return await repositories().get_purge(purge_id)

    @app.post(f"{API}/admin/reconcile", response_model=ReconcileResponse)
    async def reconcile() -> dict[str, int]:
        requeued_jobs, requeued_purges = await worker().reconcile()
        return {
            "requeued_jobs": requeued_jobs,
            "requeued_purges": requeued_purges,
        }

    return app


app = create_app()

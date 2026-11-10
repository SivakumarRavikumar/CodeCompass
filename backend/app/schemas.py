from datetime import datetime

from pydantic import BaseModel, Field


class RepositoryResponse(BaseModel):
    id: str
    display_name: str
    availability: str
    indexing_status: str
    snapshot_id: str | None = None
    active_snapshot_id: str | None = None
    pending_snapshot_id: str | None = None
    job_id: str | None = None
    error: str | None
    file_name: str
    created_at: datetime
    degradation: str | None = None


class SnapshotResponse(BaseModel):
    id: str


class JobResponse(BaseModel):
    id: str
    status: str
    progress: int
    phase: str
    error: str | None
    stages: list[dict[str, object]] = Field(default_factory=list)


class UploadResponse(BaseModel):
    repository: RepositoryResponse
    snapshot: SnapshotResponse
    job: JobResponse


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)


class Citation(BaseModel):
    id: str
    path: str
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None
    redacted: bool = True


class AnswerResponse(BaseModel):
    outcome: str
    answer: str | None = None
    message: str | None = None
    citations: list[Citation]
    trace_id: str


class TraceResponse(BaseModel):
    id: str
    query: str | None = None
    duration_ms: int | None = None
    candidates: int | None = None
    retrieved: list[dict[str, object]] | None = None
    stages: list[dict[str, object]] | None = None


class PurgeResponse(BaseModel):
    id: str
    status: str
    progress: int
    error: str | None


class ReconcileResponse(BaseModel):
    requeued_jobs: int
    requeued_purges: int

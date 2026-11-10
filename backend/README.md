# CodeCompass backend

Python 3.12 FastAPI backend for ZIP-based codebase question answering.

## Run locally

Copy the example environment, install the backend, and start Ollama with the model named in `.env` before asking a question. The example profile searches with SQLite full text. Qdrant and an embedding download are optional.

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example ../.env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

SQLite is stored at `data/codecompass.db`. With `CODECOMPASS_VECTOR_BACKEND=disabled`, indexing stays lexical-only and reports that degradation on the repository. All settings use the `CODECOMPASS_` environment prefix.

A hosted model is optional. Set `CODECOMPASS_LLM_BACKEND=bedrock` and `CODECOMPASS_BEDROCK_MODEL_ID` in `.env` to a model your own AWS account can invoke. Credentials come from the standard AWS SDK chain. This repository does not include an account, profile, model id, or guardrail. A guardrail is attached only when both identifier and version are set in `.env`.

For a dependency-free deterministic local model configuration:

```bash
export CODECOMPASS_VECTOR_BACKEND=fake
export CODECOMPASS_EMBEDDING_BACKEND=fake
export CODECOMPASS_EMBEDDING_DIMENSIONS=32
export CODECOMPASS_LLM_BACKEND=fake
```

Run checks with:

```bash
pytest
ruff check .
mypy app
```

## API lifecycle

1. `POST /api/v1/repositories` accepts multipart `display_name` and ZIP `file`
   fields plus `Idempotency-Key`. It validates archive limits and paths, redacts
   secrets in memory, stores only the redacted source mirror, and queues indexing.
2. `GET /api/v1/jobs/{id}` reports stages and progress; its `/cancel` endpoint
   fences active claims.
3. `POST /api/v1/repositories/{id}/questions` performs FTS5 + dense reciprocal-rank
   fusion, applies an abstention threshold, and returns resolved citations.
4. `GET /api/v1/traces/{id}` exposes local retrieval traces and marks responses
   `Cache-Control: no-store`. Traces include the full question and repository-relative
   paths, and the endpoint has no user authentication in the MVP.
5. `DELETE /api/v1/repositories/{id}` tombstones immediately and returns a durable
   purge pollable at `GET /api/v1/purges/{id}`.

The worker uses heartbeats, expiring leases, monotonic claim epochs, per-claim
generations, retries, and exponential backoff. A generation becomes visible only
after expected vector IDs are verified. If dense infrastructure is unavailable,
activation continues in explicitly reported lexical-only mode.
`POST /api/v1/admin/reconcile` returns `requeued_jobs` and `requeued_purges`;
protect this administrative endpoint before deployment.

Logs are structured JSON and contain identifiers/counts, never source text.
Questions, answers, citations, and traces remain until their repository is
deleted.

This backend is suitable only for the documented single-user localhost deployment.
Compose binds its host port to `127.0.0.1`; running Uvicorn on a non-loopback address
removes that protection. Remote or multi-user use requires authentication and
repository-scoped authorization, including for traces.

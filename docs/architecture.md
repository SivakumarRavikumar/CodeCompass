# Architecture

## Scope

CodeCompass answers questions about uploaded code archives. This document distinguishes the implemented MVP from future production work.

## Components

1. **ZIP ingestion boundary**
   - Accepts multipart ZIP uploads only, with an idempotency key and configured byte/entry/compression limits.
   - Rejects traversal, absolute/drive paths, symlinks, encrypted entries, duplicate paths, and suspicious archives.
   - Skips binary and non-UTF-8 files, redacts recognized secret patterns, and stores only the redacted mirror in SQLite. Valid Python uses AST source spans to replace sensitive values without persisting or embedding the original and without changing line counts.
2. **Durable single worker**
   - Claims SQLite jobs with worker identity, expiring lease, heartbeat, monotonic claim epoch, and per-claim generation ID.
   - Retries with backoff, supports cancellation, and reconciles expired claims at startup or through the admin endpoint.
   - Activates a generation only while the claim is still owned.
   - Durably records abandoned generations from failed, cancelled, or expired claims. Cleanup is separately fenced and refuses to delete active or live-worker generations.
3. **Chunker**
   - Uses top-level Python AST ranges and bounded overlapping line windows.
   - Uses line windows for non-Python or unparsable content and preserves path/symbol/inclusive lines.
4. **Lexical and vector indexes**
   - Stores chunks and an FTS5 virtual table in SQLite.
   - Embeds batches with the configured adapter and stores generation-filtered vectors in Qdrant.
   - Verifies expected Qdrant IDs before normal activation. A completed verification with a mismatched point set is an integrity failure and the generation cannot activate.
   - Requires an immutable model revision, verifies the snapshot embedding fingerprint at index and query time, and rejects incompatible Qdrant collection dimensions/distance.
5. **Hybrid retriever**
   - Retrieves SQLite FTS5 and Qdrant cosine candidates.
   - Fuses their ranks using reciprocal-rank fusion; the MVP has no reranker.
   - Uses FTS5 alone when a generation was activated in explicit dense-degraded mode.
6. **Answer and citation service**
   - Applies an RRF-score abstention threshold before generation.
   - Packs complete labeled chunks within the configured character budget and calls Ollama with `stream: false`; if no chunk fits, it abstains without generation.
   - Sends repository evidence as explicitly untrusted data with instructions not to follow source-embedded requests. This is a prompt-injection mitigation, not a guarantee.
   - Rejects any malformed or out-of-range model label and resolves valid labels to server-owned path/line/snippet citations. No valid label means abstention. Label validation proves resolvability, not semantic support.
7. **Web interface**
   - Shows ZIP upload, idempotent retry messaging, job progress/cancel, answers, citations, traces, abstention, and purge progress.
   - Sanitizes rendered Markdown.
8. **Persistence and telemetry**
   - SQLite stores lifecycle state, redacted files/chunks, questions, citations, and metadata traces.
   - Structured logs avoid source text. Traces currently persist the query, so production privacy/retention controls remain required.
9. **Evaluation harness**
   - Loads fixture/question contracts and writes prediction JSONL.
   - Scoring remains independent from retrieval and model implementations.

## Data flow

```text
ZIP
  -> validate/extract/redact -> SQLite redacted source
  -> durable claim/fencing -> chunk
  -> SQLite chunks + FTS5
  -> embed -> Qdrant -> verify -> activate

question
  -> FTS5 lexical + Qdrant dense
  -> reciprocal-rank fusion (or explicit FTS5-only degradation)
  -> score threshold -> buffered Ollama
  -> server citation validation -> answer | abstention

delete
  -> tombstone/cancel -> durable purge claim
  -> renewable lease -> verified Qdrant delete -> fenced SQLite cascade
  -> idempotency cleanup -> completed
```

## State and lifecycle

Uploads create repository, pending snapshot, embedding configuration, job, and source rows in one transaction. `active_snapshot_id` remains null until verified activation; `pending_snapshot_id` identifies work in progress and is cleared on activation or terminal cancellation/failure. Repository indexing status follows job claim, retry, reconciliation, and terminal transitions. Each worker claim receives a generation ID. Chunks and vectors are filtered by repository and generation; the snapshot points to the active generation only after verification/activation. This MVP creates one snapshot per uploaded repository rather than incremental commit synchronization.

## Failure behavior

- Invalid/non-ZIP or unsafe archives fail before job creation.
- Binary and non-UTF-8 files are skipped; an archive with no remaining UTF-8 source fails.
- For a new upload, embedding/Qdrant unavailability can activate an explicit lexical-only generation instead of failing the complete job. A verified Qdrant point-set mismatch retries or fails the job and never activates that generation.
- Expected Ollama connection, timeout, HTTP, or malformed-response failures return HTTP `503` with code `model_unavailable`; generation is buffered rather than streamed.
- Low-confidence or unsupported questions produce an explicit abstention.
- Missing valid citation labels downgrades the response to abstention.
- Runtime embedding fingerprints that differ from a queued or active snapshot fail explicitly; incompatible vectors are never queried.
- Purge renews its lease while Qdrant deletion is in flight and reports failure if deletion cannot be verified. A stale attempt cannot commit SQLite deletion or completion.
- Failed, cancelled, and crash-expired generation attempts are recovered into a durable cleanup queue. Cleanup rechecks active snapshots and valid worker claims before a generation-scoped delete.

## Extension points

Protocols isolate embedder, vector store, and LLM implementations; fake adapters support deterministic backend tests. Chunking/retrieval/services remain direct modules. There is no reranker or general workflow framework in the MVP.

## Deployment boundary

The implemented deployment is single-user and localhost-only. Compose binds the
frontend and backend host ports to `127.0.0.1`; nginx accepts only local Host
values and adds CSP and browser security headers. Trace responses are non-cacheable,
but traces still contain full questions and paths and have no user authorization.
These controls are not tenant isolation. Remote or multi-user deployment requires
authentication, repository-scoped authorization, TLS, and trace retention controls.

## Quality and observability

The implementation exposes job stages and query traces, but does not yet capture full token/model/hardware telemetry. Planned evaluation measures ingestion coverage, chunk distribution, lexical/dense/hybrid retrieval, citation validity, answerability decisions, context truncation, latency, throughput, and errors. Reports must preserve raw outputs and state limitations.


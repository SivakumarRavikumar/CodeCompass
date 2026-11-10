# CodeCompass

CodeCompass is a local-first conversational code documentation assistant for uploaded source archives. The implemented MVP accepts ZIP files, redacts recognized secrets before persistence, indexes code with SQLite FTS5 and Qdrant, and returns evidence-backed answers or an explicit abstention.

## Assessment status

- Backend and frontend unit suites, frontend build/lint scripts, and the dependency-free evaluation validator/tests are available.
- The application implements upload, durable indexing, hybrid or lexical retrieval, buffered answers, server-resolved citations, traces, cancellation, and durable purge.
- All embedding, Ollama, latency, memory, and quality results are **pending actual runs on identified hardware**.
- The fixture repositories are synthetic local examples. They are not public benchmarks and must not be described as such.
- Browser Playwright checks pass in desktop and mobile Chrome profiles. Deterministic
  UI screenshots are included; held-out model evaluation and video remain pending.

## Product experience

The React interface is organized around repository lifecycle rather than a generic chat box: ZIP upload with safe-retry messaging, repository cards with separate availability/index status, background progress and cancellation, suggested questions, evidence-aware answers, a source drawer with redacted snippets/line spans, retrieval trace details, explicit insufficient-evidence states, responsive navigation, and typed confirmation plus progress for deletion. This makes provenance and lifecycle failures visible instead of hiding them behind model output.

## Restrictions

- This is a single-user local tool. Run it on `127.0.0.1`. It has no login and no per-user authorization.
- Only ZIP uploads of text source are accepted. The default limits are 50 MB compressed, 200 MB expanded, 20,000 entries, and 5 MB per file.
- `.git`, `node_modules`, `dist`, virtualenvs, and build output are skipped after the archive is opened. A ZIP that still contains them can exceed the entry or size limit, so exclude them before you zip.
- Recognized secrets are redacted before they are stored. Redaction is not a guarantee. Do not upload private keys, credential files, or `.env` files.
- No cloud account, AWS profile, model id, or API key is included. Put local settings in `.env`. That file is gitignored and must stay uncommitted.
- The example configuration answers with a local Ollama model and searches with SQLite full text. Qdrant and embedding downloads are optional.
- Hosted generation is optional. If you set `CODECOMPASS_LLM_BACKEND=bedrock`, you must supply your own model id and AWS credentials for an account you are allowed to use.
- Traces store the question and retrieved paths. They are for the local operator only.
- The evaluation fixtures are synthetic. They are not public benchmarks, and this repository does not claim measured answer quality.

## Quick start

Requirements: Python 3.12 and [Ollama](https://ollama.com) for generated answers. Node.js is required only for the UI.

```bash
ollama pull qwen2.5-coder:3b
```

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example ../.env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000/docs>. Upload a UTF-8 source ZIP, poll the job until `status` is `ready`, then ask a question. The example profile reports lexical-only retrieval because dense search is off.

For the UI:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. `docker compose up --build` starts Qdrant with the API and UI. The Compose answer default is Ollama on the host at `127.0.0.1:11434`. `qwen2.5-coder:3b` and `sentence-transformers/all-MiniLM-L6-v2` are optional baselines, not measured winners.

## Verification commands

Run each block from the repository root in a fresh shell.

Backend unit, lint, and type checks:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,embeddings]"
pytest
ruff check .
mypy app
```

Frontend unit, build, and lint checks:

```bash
cd frontend
npm ci
npm test
npm run build
npm run lint
```

`npm run test:e2e` exercises cited-answer and abstention paths in desktop and
mobile Chrome profiles. It uses deterministic mocked API responses so browser
behavior is reproducible without claiming a live-model evaluation.

Evaluation validation and unit checks (Python 3.11+, no third-party packages):

```bash
python3 evaluation/scripts/validate_assets.py \
  --evaluation-root evaluation \
  --log-file evaluation/reports/validate.log
python3 -m unittest discover -s evaluation/tests -v
```

The evaluation package contains **12 question records**: six development and six
held-out. That number is dataset size, not a count of proven quality tests. Static
validation checks fixture manifests, evidence paths/spans, metric arithmetic,
multipart construction, polling, and mocked response mapping. It does not run
retrieval, embeddings, Qdrant, Ollama, citation support, or semantic answer review.
Only dated prediction artifacts from the live API can support retrieval and
abstention metrics, and prose factuality still requires human or separately
validated semantic review.

With the application running, produce development predictions through the real API:

```bash
MODEL_DIGEST="$(ollama list | awk '$1 == "qwen2.5-coder:3b" {print $2; exit}')"
test -n "$MODEL_DIGEST"
python3 evaluation/scripts/run_api_evaluation.py \
  --api-base-url http://localhost:8000/api/v1 \
  --questions evaluation/datasets/dev_questions.json \
  --fixtures-root evaluation/fixtures \
  --output evaluation/reports/dev_predictions.jsonl \
  --log-file evaluation/reports/api-evaluation.log \
  --poll-timeout 300 \
  --poll-interval 1 \
  --model-name qwen2.5-coder:3b \
  --model-digest "$MODEL_DIGEST" \
  --config-id mvp-fts5-qdrant-rrf-baseline \
  --config-metadata '{"embedding_model":"sentence-transformers/all-MiniLM-L6-v2","embedding_dimensions":384,"retrieval":"sqlite-fts5+qdrant-rrf","status":"experiment-pending-baseline"}' \
  --delete-after-run
```

Then score the documented JSONL contract:

```bash
python3 evaluation/scripts/score_results.py \
  --questions evaluation/datasets/dev_questions.json \
  --predictions evaluation/reports/dev_predictions.jsonl \
  --output evaluation/reports/dev_metrics.json \
  --log-file evaluation/reports/score.log
```

Do not tune against `heldout_questions.json`. Run it only after freezing retrieval, prompts, and thresholds.

## Architecture overview

```text
ZIP upload -> archive limits/path checks -> secret redaction -> SQLite source mirror
           -> durable fenced worker -> Python-aware chunks
           -> SQLite FTS5 + all-MiniLM embeddings in Qdrant

Question -> lexical + dense candidates -> reciprocal-rank fusion (RRF)
         -> score abstention -> buffered answer generation
         -> server citation validation -> answer/abstention + metadata trace

Delete -> immediate tombstone -> durable purge -> verified Qdrant deletion
       -> SQLite repository cascade
```

The request path is local-first. Only ZIP uploads are accepted. Archive traversal, symlinks, encrypted entries, duplicate paths, compression bombs, oversized content, binary files, and non-UTF-8 files are rejected or skipped as implemented. Recognized secrets are replaced before the redacted source mirror is stored. For valid Python, sensitive assignment, keyword, and dictionary values are replaced using AST source spans so only redacted text reaches chunking, persistence, and embedding while syntax and line counts remain stable. See [architecture](docs/architecture.md), [security](docs/security-threat-model.md), and the [ADRs](docs/adrs/).

## RAG and LLM decisions

The evaluation plan compares choices before declaring a winner:

- **Chunking:** the MVP uses top-level Python AST symbols and bounded overlapping line windows; non-Python and unparsable files use line windows. Experiments compare sizes/overlaps without changing the provenance contract.
- **Embeddings:** `sentence-transformers/all-MiniLM-L6-v2` at 384 dimensions is the implemented baseline. Its repository revision is pinned in configuration; mutable aliases such as `main` are rejected by the real adapter. Snapshot fingerprints are checked before indexing and querying, and an incompatible Qdrant collection fails rather than silently degrading or querying incompatible vectors. Candidate changes require a new embedding configuration and generation.
- **Retrieval:** SQLite FTS5 lexical candidates and Qdrant cosine dense candidates are fused with reciprocal-rank fusion (`rrf_k=60` by default). There is no reranker in the MVP.
- **Alternatives considered:** lexical-only is simpler and remains the fallback but misses semantic matches; dense-only loses exact identifier/token strengths; a learned reranker adds latency and another model before baseline evidence exists. FTS5 + Qdrant RRF was chosen because it combines complementary ranks without cross-backend score normalization.
- **Lexical-only degraded mode:** if Qdrant or embedding is unavailable during ingestion, the generation can activate with an explicit repository degradation. A completed Qdrant verification that finds a mismatched point set is an integrity failure instead: the job retries or fails and that generation never activates. Questions for explicitly degraded repositories bypass dense retrieval and use FTS5 only.
- **Orchestration:** plain Python services/adapters coordinate durable SQLite jobs; no orchestration framework is used. Failed, cancelled, or expired attempts enqueue durable generation cleanup. Cleanup rechecks active snapshots and live claims before deleting only the abandoned Qdrant generation.
- **Generation:** buffered (`stream: false`) Ollama with `qwen2.5-coder:3b` is the implemented baseline. It is experiment-pending, not a winner. Planned 3B/7B and context measurements must record exact digest, quantization, Ollama version, and hardware.
- **Prompt/context:** complete labeled chunks are packed in retrieval order within the configured character budget. If the highest-ranked chunk cannot fit, the service abstains without calling the model. Repository context is sent as explicitly untrusted data and the model is instructed not to follow instructions within it; this mitigates prompt injection but does not eliminate it.
- **Guardrails:** bounded ZIP extraction, path/symlink/encryption checks, secret redaction, score-based abstention, and strict server-side rejection of malformed or out-of-range citation labels. Markdown is sanitized in the frontend. Citation-label validation establishes resolvability, not semantic correctness.
- **Quality:** report retrieval hit/recall, citation precision, answerability/abstention classification, selective accuracy, and latency distributions. Never collapse these into an unsupported single “accuracy” claim.
- **Observability:** structured backend logs avoid source text; persisted metadata traces record query, duration, candidates, retrieval spans/scores, mode, and degradation. Production needs stronger retention and query-privacy controls.

Experiment definitions live in `evaluation/configs/`. Calibration is performed on development questions only.

## API summary

All routes use `/api/v1`.

- `GET /api/v1/health`: liveness response.
- `POST /repositories`: multipart `display_name` plus ZIP `file`; requires `Idempotency-Key`; returns repository, snapshot, and queued job.
- `GET /repositories` and `GET /repositories/{id}`: list or inspect repository availability/index state and degradation.
- `GET /jobs/{id}` and `POST /jobs/{id}/cancel`: poll or fence/cancel indexing.
- `POST /repositories/{id}/questions`: ask a question; returns `answered` or `insufficient_evidence`, server-resolved citations, and a trace ID.
  Expected model connection, timeout, credential, or malformed-response failures return HTTP `503` with `{"detail":"local answer model is unavailable","code":"model_unavailable"}`.
- `GET /traces/{id}`: retrieve persisted local trace data. Traces include the full
  question and repository-relative paths, are returned with `Cache-Control: no-store`,
  and are only acceptable within the single-user loopback deployment.
- `DELETE /repositories/{id}` and `GET /purges/{id}`: tombstone, then poll durable verified purge. Purge leases renew during Qdrant deletion, stale attempts are fenced from SQLite completion, and repository idempotency records are removed with the SQLite repository transaction.
- `POST /admin/reconcile`: requeue expired leases and return `requeued_jobs` plus
  `requeued_purges`; it is unprotected in the MVP and must be restricted before deployment.

OpenAPI is exposed by FastAPI at <http://localhost:8000/docs> in the local configuration.

## Engineering standards

- Typed FastAPI/Pydantic/SQLAlchemy backend and strict TypeScript frontend.
- Idempotent uploads, explicit lifecycle states, durable leases, monotonic claim epochs, generation fencing, retries, cancellation, startup reconciliation, and verified deletion.
- Configurable limits/models/thresholds through `CODECOMPASS_` settings.
- Structured logging without source text; bounded operator-facing errors.
- Evaluation CLIs use `argparse`, timestamped file logging, actionable validation errors, and non-zero exit codes.
- Backend pytest/ruff/mypy, frontend Vitest/build/ESLint, and focused standard-library evaluation tests are defined.
- Separate development and held-out sets, stable IDs, pinned fixture manifests, and machine-readable outputs.
- Explicit provenance and “pending” labels prevent synthetic fixtures from becoming misleading benchmark claims.
- Documentation records trade-offs, threats, deployment gaps, and skipped work.

Available code/checks are not the same as completed model evidence. Unit,
build, lint, and deterministic browser checks are implemented. Actual
model/hardware measurements, a frozen held-out run, load/soak tests, restore
exercises, live-model screenshots, and video remain pending unless linked from
a dated report.

## Evaluation workflow

1. Validate fixture hashes, paths, symbols, line spans, categories, and split isolation.
2. Freeze one experiment configuration and record git revision plus environment metadata.
3. ZIP and ingest only the fixture named by each question through the real API.
4. Save one prediction per question, including retrieved citations, answer/abstention, timings, and model identity.
5. Tune abstention thresholds using the development split.
6. Freeze thresholds and run held-out once.
7. Render a report from the template without deleting failed cases.

The metric contract and prediction schema are in [evaluation/README.md](evaluation/README.md).

## Demo and evidence

- [Demo script](demo/demo-script.md)
- [Screenshot checklist](demo/screenshot-checklist.md)
- [Cited-answer screenshot](demo/screenshots/cited-answer.png)
- [Abstention screenshot](demo/screenshots/abstention.png)
- [Pending report template](evaluation/reports/REPORT_TEMPLATE.md)

Screenshots should show ingestion provenance, a cited answer, an honest abstention, and diagnostics without exposing secrets or absolute user paths.
The included screenshots come from the deterministic browser suite. Capture a
second set against the live Ollama/Qdrant environment after the experiment
configuration is frozen. A short video remains optional.

## Productionization and scale

The prototype-to-production path is documented in [productionization](docs/productionization.md). In brief: isolate tenant data, perform ingestion in queued workers, use object storage and a managed/persistent vector index, autoscale stateless APIs, enforce quotas, encrypt data, add deletion/export workflows, and evaluate every model/index release. Cloud deployment details depend on the selected provider and data-residency requirements.

## Key decisions and trade-offs

- Synthetic fixtures make tests safe and deterministic, but do not establish performance on real repositories.
- Hybrid FTS5/Qdrant RRF improves recall diversity without a reranker, but requires separate lexical/vector consistency and generation fencing.
- Lexical-only activation preserves availability during dense-index failure, but it is an explicit quality-degraded state.
- Local Ollama improves source-code privacy, but hardware, quantization, context, and concurrency materially affect quality and latency.
- Buffered generation simplifies citation validation and the response contract, but does not provide token streaming.
- Line-span grading is deterministic, but refactors require dataset updates and hash validation.
- Abstention is a first-class outcome. Coverage and correctness must be reported together.

See [ADR 001](docs/adrs/001-evaluation-fixtures.md) and [ADR 002](docs/adrs/002-local-rag-boundaries.md).

## AI-assisted development policy

AI assistance was used to draft and review implementation, tests, evaluation fixtures, and documentation. It was constrained by explicit architecture/scope, checked against backend/frontend contracts, and followed by deterministic validation rather than accepted as authority. A human still owns requirements, security boundaries, dependency/model choices, experiment interpretation, assessment prose, and every published claim. Generated changes require diff review and executable checks; secrets and proprietary repositories must not be pasted into unapproved services. Full do/don’t and repeatability policy: [AI-assisted development](docs/ai-assisted-development.md).

## With more time

- Add parser coverage for more languages and generated/vendor-file detection.
- Add adversarial prompt-injection and secret-leakage cases.
- Run repeated experiments on named hardware and publish raw artifacts with confidence intervals.
- Run and harden Playwright accessibility/browser tests, concurrency, resilience, purge, and restore testing.
- Add authenticated tenant isolation, protected administration, streaming with citation-safe finalization, and repository connectors only after the ZIP workflow is secure.
- Record a short demo only after the UI and reported measurements are reproducible.

## Documentation index

- [Architecture](docs/architecture.md)
- [Security and threat model](docs/security-threat-model.md)
- [Productionization](docs/productionization.md)
- [AI-assisted development policy](docs/ai-assisted-development.md)
- [Evaluation package](evaluation/README.md)


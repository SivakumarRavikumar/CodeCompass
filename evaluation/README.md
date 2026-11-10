# Evaluation package

This package is a reusable, adapter-neutral contract for evaluating CodeCompass. It ships with two miniature synthetic Python repositories, development and held-out questions, experiment configurations, validation/scoring scripts, tests, and a report template.

It contains **no measured model results**. Fixture scores are not evidence of public benchmark performance.

The two dataset files contain 12 question records in total: six development and
six held-out. They are evaluation inputs, not 12 passing quality tests. The
dependency-free test suite validates assets, path/span contracts, metric
arithmetic, multipart generation, polling behavior, and mocked API mapping. It
does not invoke the production retriever, embedding model, Qdrant, or Ollama.

The application architecture under evaluation is ZIP ingestion, redaction, durable fenced indexing, top-level Python AST/line chunking, SQLite FTS5 plus Qdrant cosine retrieval fused with RRF, score abstention, buffered Ollama generation, and server-resolved citations. `all-MiniLM-L6-v2` and `qwen2.5-coder:3b` are runnable experiment-pending MVP baselines, not winners. The API runner is implemented, but no live model, hardware, or held-out run is claimed until prediction/report artifacts are recorded.

## Layout

- `fixtures/`: safe repositories plus pinned SHA-256 manifests.
- `datasets/`: independent question splits.
- `configs/`: candidate experiment and calibration definitions.
- `scripts/run_api_evaluation.py`: ZIPs/uploads each fixture once, polls indexing, asks questions, fetches traces, and writes prediction JSONL.
- `scripts/validate_assets.py`: validates manifests and question evidence.
- `scripts/score_results.py`: computes deterministic retrieval/citation/abstention metrics.
- `tests/`: focused standard-library tests.
- `reports/`: generated artifacts (ignored by convention) and the report template.

## Question contract

Each question has:

- stable `id`, `split`, `fixture`, `category`, and natural-language `question`;
- `answerable`, defining whether fixture evidence can answer it;
- `expected_evidence`, a list of repository-relative `path`, optional `symbol`, and inclusive `[start, end]` line spans;
- `notes`, for evaluator intent rather than a model-ready reference answer.

Categories cover architecture, symbol location, behavior, dependencies, and unanswerable requests. Expected evidence grades provenance; it is not injected into prompts.

## Prediction JSONL contract

One JSON object per line:

```json
{
  "question_id": "dev-atlas-001",
  "answer": "The function normalizes the input...",
  "abstained": false,
  "citations": [
    {"path": "atlas_text/normalize.py", "start_line": 4, "end_line": 8}
  ],
  "retrieved": [
    {"path": "atlas_text/normalize.py", "start_line": 3, "end_line": 9, "rank": 1, "score": 0.71}
  ],
  "latency_ms": 1250,
  "model": {"name": "local-model", "digest": "record-the-real-digest"},
  "config_id": "symbol-local-small"
}
```

For an abstention, set `abstained: true`, provide a concise reason in `answer`, and use empty citations unless evidence supports the explanation. Unknown question IDs, duplicates, malformed spans, and missing predictions are errors.

## Metrics

- `retrieval_hit_at_k`: answerable questions with any expected evidence overlapped by a top-k retrieved span.
- `citation_precision`: cited spans overlapping expected evidence divided by all returned citations; citations on unanswerable questions therefore count as unsupported.
- `citation_coverage`: answered, answerable questions with at least one matching citation.
- `answerability_accuracy`: correct answer-versus-abstain classification.
- `unanswerable_abstention_rate`: unanswerable questions correctly abstained.
- `answerable_coverage`: answerable questions not abstained.
- `selective_retrieval_accuracy`: retrieval hit rate among answered, answerable questions.

Span overlap requires the same path and intersecting inclusive lines. Symbol metadata is validated in the dataset but not required in prediction records.

These metrics do not judge prose factuality. Human review or a separately validated rubric is still required.

Coverage is intentionally separated:

- **Static asset validation:** proves fixture hashes, question shape, safe relative
  paths, and expected-evidence spans are internally consistent.
- **Harness unit tests:** prove deterministic metric and API-runner mechanics using
  local or mocked values.
- **Live development run:** can measure this configured system's retrieval,
  citation-span, abstention, and latency behavior after raw predictions are saved.
- **Held-out run:** estimates generalization only after configuration is frozen.
- **Semantic answer review:** remains separate; valid/resolvable citations and span
  overlap do not prove that every generated claim is correct.

## Commands

Run from the repository root. First validate the static assets and harness:

```bash
python3 evaluation/scripts/validate_assets.py \
  --evaluation-root evaluation \
  --log-file evaluation/reports/validate.log
python3 -m unittest discover -s evaluation/tests -v
```

With Docker Compose and Ollama already running, capture the installed model identifier:

```bash
MODEL_DIGEST="$(ollama list | awk '$1 == "qwen2.5-coder:3b" {print $2; exit}')"
test -n "$MODEL_DIGEST"
```

Run the development split through the live API. Each fixture is zipped in memory and uploaded once, then reused for all its questions. `--delete-after-run` waits for durable purge after predictions are collected.

```bash
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

Score the completed development predictions:

```bash
python3 evaluation/scripts/score_results.py \
  --questions evaluation/datasets/dev_questions.json \
  --predictions evaluation/reports/dev_predictions.jsonl \
  --output evaluation/reports/dev_metrics.json \
  --retrieval-k 5 \
  --log-file evaluation/reports/score.log
```

All three scripts support `--help`, `--log-level`, and `--version`. The runner returns nonzero for invalid configuration, HTTP failures, malformed responses, terminal job/purge failures, and poll timeouts. It writes final JSONL atomically only after every question succeeds. Run `heldout_questions.json` only after the development configuration and abstention threshold are frozen.

Backend/frontend verification is separate from this dependency-free harness.
See the root README for pytest/ruff/mypy, Vitest/build/ESLint, and Playwright
commands. Deterministic desktop/mobile browser checks have run; actual
model/hardware measurements and held-out execution remain pending until dated
artifacts record them.

## Reproducibility protocol

Record the repository revision, fixture manifest version, question set version, complete config, model names and digests, quantization, Ollama version, OS, CPU/GPU/RAM, cold/warm status, and raw per-question output. Repeat timing runs, preserve failures, and report sample count plus p50/p95—not only the fastest result.


# Demo script

Target length: 4–6 minutes. Replace bracketed items only after verifying the running application. Do not quote pending evaluation numbers.

## Preparation

1. Create a ZIP from `evaluation/fixtures/atlas_text`; do not upload private source.
2. Pull `qwen2.5-coder:3b`, copy `.env.example` to `.env`, and start with `docker compose up --build`.
3. Confirm <http://localhost:8080> and Ollama availability; clear unrelated logs/history.
4. Validate evaluation assets and keep the successful command output available.
5. Set browser zoom and hide notifications, usernames, absolute paths, tokens, and unrelated tabs.

From the repository root, create the demo archive:

```bash
(cd evaluation/fixtures/atlas_text && zip -r /tmp/codecompass-atlas-text.zip .)
```

Upload `/tmp/codecompass-atlas-text.zip` in the UI.

## Walkthrough

### 1. Problem and boundaries

“CodeCompass helps a developer understand a ZIP of source code using retrieval-grounded answers. The demo uses a synthetic local fixture; it is not a public benchmark. The default all-MiniLM embedding model and Qwen 3B generator are experiment-pending baselines, not measured winners.”

Show repository selection and the local-processing/privacy explanation.

### 2. Ingestion

Upload the prepared `atlas_text` ZIP. Show:

- upload progress and idempotent retry messaging;
- queued/processing progress and cancellation control;
- successful `ready` index state;
- any visible lexical-only degradation if dense indexing failed.

The current UI does not expose complete model/chunk configuration or skipped-file counts; use API/job metadata or logs only if needed and do not claim they are visible in the UI. Do not imply that the fixture proves large-repository scalability.

### 3. Grounded answer

Ask: “Where are duplicate document identifiers rejected?”

Expected behavior: the answer cites `atlas_text/catalog.py` around `Catalog.add`. Open the citation and confirm the server-returned redacted snippet and lines. Explain that model labels are validated and resolved to server-owned citations.

### 4. Behavior nuance

Ask: “Does normalizing a title lowercase it?”

Expected behavior: distinguish `normalize_title` from `slugify`, cite the relevant symbols, and avoid inventing behavior.

### 5. Abstention

Ask: “Which database stores catalog data in production?”

Expected behavior: state that the fixture does not provide that evidence and abstain. Explain that the current score threshold is an experiment-pending baseline; calibration is development-only and held-out settings must be frozen.

### 6. Engineering evidence

Show the evaluation README, split question files, pinned fixture manifest, FTS5/Qdrant RRF experiment configs, and pending report template. Run:

```bash
python3 evaluation/scripts/validate_assets.py \
  --evaluation-root evaluation \
  --log-file evaluation/reports/validate.log
python3 -m unittest discover -s evaluation/tests -v
```

### 7. Close

Summarize ZIP limits/redaction, durable fenced work, FTS5+Qdrant RRF with
lexical-only degradation, server citations, explicit abstention, purge, and
production gaps. The deterministic desktop/mobile Playwright checks pass;
state clearly that live hardware/model and held-out runs remain incomplete.

## Failure fallback

If model serving fails, show the error state and validation assets; do not substitute fabricated output. If a response is unsupported, use it to explain the quality gap and preserve it in raw evaluation artifacts.


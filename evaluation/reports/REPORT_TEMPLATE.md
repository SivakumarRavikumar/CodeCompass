# CodeCompass evaluation report

> **Status: PENDING.** No results should be entered or summarized until actual embedding, Ollama, and hardware runs complete. Blank fields are not zero. This report is not a public benchmark.

## Run identity

- Run ID: PENDING
- Date/time and timezone: PENDING
- Code revision: PENDING
- Dataset/split/version: PENDING
- Fixture manifest versions: PENDING
- Configuration ID and attached config: PENDING
- Raw prediction artifact and SHA-256: PENDING
- Metrics artifact and SHA-256: PENDING

## Environment

- OS/version: PENDING
- CPU/cores: PENDING
- RAM: PENDING
- GPU/accelerator and memory: PENDING
- Ollama version: PENDING
- Model name/digest/quantization: PENDING
- Embedding provider/model/revision or digest: PENDING
- Qdrant version/collection and vector dimensions: PENDING
- SQLite version/FTS5 availability: PENDING
- Cold/warm state and concurrency: PENDING

## Retrieval and chunking

- ZIP validation/redaction policy and limits: PENDING
- Chunk strategy/size/overlap: PENDING
- Corpus files/chunks/bytes: PENDING
- Index build duration and peak memory: PENDING
- lexical/dense candidate limits, retrieval limit, and RRF k: PENDING
- Retrieval mode counts (hybrid/dense-only/lexical-only): PENDING
- Dense degradation/failures: PENDING
- Retrieval hit@k: PENDING — include numerator/denominator

## Grounding and abstention

- Citation precision: PENDING — include numerator/denominator
- Citation coverage: PENDING — include numerator/denominator
- Answerability accuracy: PENDING — include confusion counts
- Unanswerable abstention rate: PENDING
- Answerable coverage: PENDING
- Selective retrieval accuracy: PENDING
- Human factuality review rubric/results: PENDING

## Ollama and context measurements

For the buffered Qwen 3B MVP baseline and each 7B candidate/context size, record repetition count, p50/p95 end-to-end latency, prompt/output throughput, peak process/GPU memory, context truncations, errors, and citation validity. If time to first token is measured with a separate streaming experiment, label it as non-MVP behavior. Attach raw per-run records.

Results: PENDING

## Failed cases

Preserve every retrieval miss, unsupported citation, incorrect answerability decision, timeout, truncation, and malformed output. Do not remove warm or slow runs without a predeclared exclusion rule.

Failed-case analysis: PENDING

## Interpretation

- What the data supports: PENDING
- What it does not support: PENDING
- Fixture/external-validity limitations: PENDING
- Statistical uncertainty and run-to-run variance: PENDING
- Decision and trade-offs: PENDING

## Reproduction

List exact validation, application, prediction, scoring, and environment-capture commands. Confirm that thresholds were calibrated on development data and frozen before held-out execution.

Commands and outcome: PENDING

## Application verification status

- Backend pytest/ruff/mypy: PENDING
- Frontend Vitest/build/ESLint: PENDING
- Evaluation validator/unit tests: PENDING
- Browser Playwright: PENDING
- Screenshots/video: PENDING


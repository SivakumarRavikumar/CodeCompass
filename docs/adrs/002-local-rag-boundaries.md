# ADR 002: Use FTS5/Qdrant RRF with local generation

- Status: Accepted for MVP; model quality remains experiment-pending
- Date: 2026-11-02

## Context

Code repositories may be proprietary. A local-first stack reduces disclosure risk. Lexical and semantic retrieval have complementary failure modes, and dense infrastructure should not make an already-redacted repository unusable. Model and hardware quality trade-offs have not yet been measured.

## Decision

Use SQLite FTS5 for lexical retrieval and Qdrant cosine search for dense retrieval, fused by reciprocal rank fusion. Use lexical-only mode when dense indexing is unavailable and expose that degradation on the repository and trace. Generate buffered answers through Ollama and accept citations only when model `[C#]` labels resolve to server-supplied chunks.

The Compose defaults `sentence-transformers/all-MiniLM-L6-v2` and `qwen2.5-coder:3b` are baselines for a runnable MVP, not evaluation winners. Freeze model digests, thresholds, and context settings only after development experiments; do not tune on held-out questions.

## Consequences

- Local source remains under operator control by default.
- FTS5/Qdrant adds operational coordination; generation IDs, filtered queries, vector verification, and durable purge reduce stale/cross-repository risks.
- Lexical-only mode preserves availability but can reduce semantic recall and must remain visible.
- RRF avoids score normalization across backends but adds a tunable rank constant and has no learned reranking.
- Ollama availability, model license, digest, quantization, context size, and hardware become deployment inputs.
- Buffered generation simplifies citation validation but delays the complete response.
- Avoiding a heavy orchestration framework reduces coupling but places durable lifecycle logic in explicit services.
- This ADR records the implemented MVP architecture, not benchmark results or a production model commitment.


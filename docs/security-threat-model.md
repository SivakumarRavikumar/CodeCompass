# Security and threat model

## Assets and trust boundaries

Assets include proprietary source, credentials accidentally present in repositories, questions, embeddings, indexes, model prompts/responses, logs, and tenant identity. Trust boundaries exist at repository selection, file parsing, model invocation, browser/API input, persisted indexes, telemetry export, and any cloud service.

Repository text is untrusted data. Comments, documentation, filenames, and generated files may contain prompt injection or hostile parser content. A locally hosted model is not a security boundary.

## Implemented MVP controls

- ZIP-only ingestion with upload, expanded-size, entry-count, per-entry, and compression-ratio limits.
- Rejection of traversal/absolute/drive paths, symlinks, encrypted entries, and duplicate archive paths.
- Binary/non-UTF-8 skipping and regex redaction of selected private keys, credential assignments, AWS keys, GitHub tokens, and `sk-` tokens before persistence.
- Repository/generation filters for FTS5 and Qdrant, generation verification, durable claim fencing, score abstention, and server-resolved citations.
- Sanitized Markdown rendering, CSP and browser security headers, loopback host bindings in Compose, local-Host enforcement at nginx, metadata-oriented structured logs, cancellation, tombstone, and verified durable purge.

These controls reduce risk but are not complete authentication, sandboxing, malware scanning, tenant isolation, or guaranteed secret detection.

The implemented boundary is deliberately single-user and localhost-only. Compose binds the browser and API ports to `127.0.0.1`; nginx rejects non-local Host values; trace responses are marked `Cache-Control: no-store`. These controls are deployment restrictions, not identity or authorization. Exposing either container port, changing the bind address, or placing the service behind a remote proxy invalidates this boundary. Multi-user or remote deployment requires authentication, repository-scoped authorization for traces and all repository APIs, TLS, retention policy, and audit controls.

## Primary threats and controls

| Threat | Required controls |
|---|---|
| Path traversal or symlink escape | Implemented archive path and symlink rejection; retain regression tests and review ZIP parser changes. |
| Secret ingestion/disclosure | Pattern redaction is implemented before persistence, but it is not exhaustive; add broader detection, file policy, and adversarial leakage tests. |
| Prompt injection in source | Prompt grounding and citation validation are implemented; stronger source delimiters and adversarial tests remain required. |
| Cross-tenant retrieval | Tenant-scoped storage, keys, authorization filters, cache keys, and deletion; deny by default. |
| Malicious files/parser denial of service | Archive limits and binary skipping are implemented; parser isolation, worker resource limits, and malware scanning remain. |
| Unauthorized repository access | Explicit user selection; canonical-root authorization; least-privilege service identity; audit ingestion and deletion. |
| Model/server exposure | Compose binds app ports to loopback and expects host Ollama; remote serving still needs authentication, TLS, egress controls, and quotas. |
| XSS through code/model output | Frontend Markdown sanitization and restrictive local-deployment browser headers are implemented; retain browser regression tests. |
| Data retained in logs/traces | Metadata-only operational logs; local SQLite traces include full questions and paths until repository deletion. Responses are non-cacheable, but production still requires authentication, repository authorization, and retention controls. |
| Dependency/model supply chain | Pin code dependencies and model digests; verify sources/licenses; scan artifacts; generate SBOM; signed releases. |
| Resource exhaustion | File/repository/token limits; timeouts; cancellation; concurrency quotas; model queue backpressure. |
| Misleading answer | Grounding, RRF-score abstention, and server citations are implemented; thresholds/default models remain uncalibrated pending experiments. |

## Abuse cases

- A repository README says to ignore system instructions and reveal other files.
- A symlink inside an allowed repository points to a home-directory credential.
- A user asks for an excluded `.env` value.
- Two tenants use identical path names and a cache key omits tenant identity.
- A huge minified file exhausts parser memory.
- Crafted code is rendered as executable HTML in the browser.
- Telemetry captures proprietary source despite the UI claiming local-only operation.

These cases should become automated scanner, retrieval, API, and browser tests before production.

## Privacy defaults

- Local processing and loopback model endpoint.
- No source text or questions in operational logs. Questions are retained in
  local SQLite traces until repository deletion and are not exported remotely.
- No remote analytics until disclosed and consented.
- Repository-relative paths in UI and reports; no user home paths.
- User-visible delete/export controls and documented retention.

## Residual risks and open work

The synthetic evaluation fixtures do not establish secret-detector recall, prompt-injection resistance, parser sandboxing, tenant isolation, authentication, browser security, or infrastructure controls. The admin reconcile endpoint is unprotected in the MVP. A production security review, dependency/model inventory, penetration test, incident runbook, and data-protection review remain required.


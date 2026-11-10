# Productionization and hyperscaler deployment

## Gaps before production

The local assessment architecture is not a production deployment. The MVP already has SQLite-backed durable jobs/purges, fencing, retries, reconciliation, and Qdrant generation verification, but runs a single in-process worker and has no authentication or tenant model. Required work includes identity and authorization, tenant isolation, scalable queues/workers, quotas, backups/restores, migrations, SLOs, alerting, incident response, security review, accessibility, load/resilience testing, and legal review of model/dependency licenses.

## Scalable reference shape

- **Edge/API:** managed load balancer or API gateway, TLS, WAF, OIDC, rate limits, and stateless application instances.
- **Ingestion:** preserve the ZIP validation/redaction boundary, store uploads in private object storage, and publish jobs to a managed queue. Replace the single worker with autoscaled workers while retaining leases, fencing epochs, idempotency, generation verification, and purge semantics.
- **Metadata:** managed relational database stores tenants, repositories, jobs, index generations, and audit records.
- **Search:** preserve lexical+dense RRF and visible degradation. Move FTS5 to a horizontally appropriate lexical service if SQLite is outgrown; operate managed Qdrant or another tenant-filtered vector service. Exact choices follow corpus, failure, and latency measurements.
- **Models:** private managed endpoints or autoscaled GPU/accelerator nodes. Ollama is useful locally; production serving needs concurrency, health, rollout, and utilization controls.
- **Observability:** OpenTelemetry traces/metrics/logs with content capture disabled by default, dashboards, SLOs, cost attribution, and audit trails.
- **Delivery:** pinned builds, SBOM, vulnerability/model scanning, signed artifacts, infrastructure as code, staged rollout, and rollback.

AWS equivalents could use ALB/API Gateway, ECS/EKS, S3, SQS, RDS, OpenSearch or managed/self-hosted Qdrant, a managed model endpoint, KMS, Secrets Manager, CloudWatch, and OpenTelemetry. GCP and Azure have analogous managed services. Cloudflare can serve the edge/UI and lightweight coordination; repository ingestion and larger model workloads may still require regional compute. These are options, not a finalized bill of materials.

## Isolation and data lifecycle

Every object, row, vector, cache entry, trace, and queue message carries tenant identity. Authorization is enforced server-side before retrieval. Use tenant-specific encryption keys where risk warrants it. Define retention, export, deletion propagation, legal hold, region residency, backup, restore, and disaster-recovery procedures. Verify that deletion removes derived chunks, vectors, caches, and logs.

## Reliability and rollout

- Idempotent ingestion keyed by source snapshot and configuration.
- Immutable index generations with atomic activation and rollback.
- Dead-letter queues, bounded retries, timeouts, cancellation, and backpressure.
- Multi-zone managed state; tested backups and restoration targets.
- Health/readiness probes and graceful degradation to search-only or explicit unavailability.
- Canary releases gated by development plus held-out evaluation, security checks, latency, and cost.

## Capacity and cost

Benchmark representative repository sizes and query mixes. Model ingestion bytes, chunks, embedding throughput, vector growth, retrieval p95, prompt tokens, accelerator memory, generation throughput, concurrency, and cache hit rate. Set per-tenant budgets and admission control. Compare 3B/7B quality and total cost on named hardware before selecting a serving tier.

## Quality operations

Maintain versioned development, held-out, regression, security, and customer-approved datasets. Re-evaluate changes to scanner, chunker, embeddings, index, retrieval, prompt, model, or quantization. Review abstentions and unsupported citations without silently training on held-out cases.

## Production readiness gates

1. Threat model controls implemented and independently reviewed.
2. Tenant isolation, authorization, deletion, backup, and restore tests pass.
3. Measured quality/latency/cost thresholds pass on frozen configurations.
4. Load, soak, failure-injection, and rollback exercises pass.
5. Dashboards, alerts, runbooks, ownership, SLOs, and incident procedures exist.
6. Privacy, license, accessibility, and regional compliance approvals complete.


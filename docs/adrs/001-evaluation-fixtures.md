# ADR 001: Use synthetic, pinned evaluation fixtures

- Status: Accepted
- Date: 2026-11-02

## Context

Evaluation needs deterministic code and evidence spans without licensing, network, privacy, or repository-drift risk. Calling tiny custom examples a public benchmark would overstate external validity.

## Decision

Maintain two small synthetic Python repositories in-tree. Every fixture has explicit provenance, version metadata, and SHA-256 hashes for all source/configuration files. Questions use repository-relative paths, symbols, and inclusive line spans. Development and held-out IDs are separate.

## Consequences

- Validation is fast, safe, offline, and reproducible.
- Evidence drift fails before scoring.
- The fixtures can test harness behavior but cannot establish quality on real or multi-language repositories.
- Any fixture edit requires a deliberate manifest and dataset update.
- Future external datasets need license, revision, acquisition, and contamination records.


# AI-assisted development policy

## Purpose

AI assistance can accelerate routine work, but it does not own requirements, design decisions, security judgments, or claims. A named human reviewer remains accountable for every merged change and published result.

## Appropriate uses

- Drafting focused tests, fixture code, documentation outlines, and reversible refactors.
- Explaining unfamiliar code as a starting hypothesis.
- Generating alternatives whose trade-offs are independently checked.
- Reviewing diffs for omissions after deterministic tools pass.

## Prohibited or restricted uses

- Do not send credentials, personal data, customer code, or unapproved proprietary repositories to external models.
- Do not accept generated dependencies, licenses, shell commands, migrations, security controls, or benchmark claims without verification.
- Do not let an assistant publish, deploy, merge, or modify production data without the project's approval controls.
- Do not represent AI-written rationale as personal experience or fabricate experiments.
- Do not use generated tests as the only evidence when they merely mirror generated implementation.

## Repeatable workflow

1. Human states scope, constraints, acceptance criteria, and data classification.
2. Assistant proposes or edits within that scope.
3. Human reviews the diff and checks assumptions against authoritative code/docs.
4. Formatters, linters, type checks, tests, secret scans, and asset validators run.
5. Security-sensitive changes receive a threat-focused review.
6. Experiment claims link to raw artifacts, exact configuration, revision, and hardware.
7. Human edits assessment prose so it reflects actual decisions and understanding.

## Traceability

Record material AI assistance in the pull request or development notes: tool/model when known, task performed, validation run, and substantive human corrections. Do not copy private prompts or source content into public records.

## Applied to these assets

The deterministic fixture hashes, validators, tests, question splits, and “results pending” labels are controls against plausible but unsupported generated claims. Before submission, the author should review and rewrite decision narratives in their own voice, run all checks, and ensure the docs match the implemented application.


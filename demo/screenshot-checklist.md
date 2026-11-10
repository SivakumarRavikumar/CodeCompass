# Screenshot checklist

Capture screenshots only from a verified local run. Use synthetic fixture data and redact usernames, absolute paths, secrets, notifications, browser profiles, and unrelated repositories.

## Required application evidence

- [ ] Landing/empty state explains repository scope and local/privacy behavior.
- [ ] ZIP upload uses an allowed synthetic fixture and shows retry-safe progress.
- [ ] Durable indexing shows queued/processing/ready state; do not claim skipped counts/configuration are visible if they are not.
- [ ] Main conversation view shows a complete, well-designed interaction.
- [ ] Grounded answer includes repository-relative path, symbol if available, and line span.
- [ ] Citation opens the exact source context.
- [ ] Unanswerable question produces a clear abstention.
- [ ] Error state is actionable and does not expose a stack trace or source content.
- [ ] Narrow/mobile viewport remains readable and keyboard focus is visible.

## Engineering evidence

- [ ] Fixture manifest and provenance disclaimer.
- [ ] Development and held-out split files.
- [ ] Successful asset validation and test output.
- [ ] A documentation/config screenshot labels all-MiniLM and Qwen 3B as experiment-pending baselines, not winners.
- [ ] Retrieval trace shows timings/mode metadata without exposing unredacted secrets.
- [ ] Delete confirmation and durable purge progress are captured.

## Capture quality

- [ ] Consistent viewport, theme, zoom, and seeded fixture.
- [ ] No placeholder copy, broken states, debug overlays, or cursor covering content.
- [ ] Captions identify scenario and expected behavior.
- [ ] Alt text is prepared for README images.
- [ ] Image filenames are stable and descriptive.
- [ ] Every pictured claim matches the running revision.

Suggested filenames: `01-empty-state.png`, `02-ingestion.png`, `03-cited-answer.png`, `04-source-detail.png`, `05-abstention.png`, `06-mobile.png`.

Optional video: record the same upload → ready → cited answer → abstention → purge flow only after it is reproducible. If browser Playwright has not run, state that separately rather than implying screenshots are automated test evidence.


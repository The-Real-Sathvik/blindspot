# Start here: Blindspot continuation

1. Read `AGENTS.md`, then [BLINDSPOT_HANDOVER.md](BLINDSPOT_HANDOVER.md), before editing.
2. Treat source code and current database state as authoritative when documentation conflicts.
3. Make small, targeted, verifiable edits. Report files changed, focused tests run, actual results, and remaining blockers.
4. Preserve the working Gemini Claim Extraction V3 contract: `gemini-3.5-flash-lite`, `MINIMAL`, `plain_text`, 512 output tokens, strict `CLAIM`/`EVIDENCE_ID` or `ABSTAIN` parsing.
5. Preserve exact source provenance: paper/job ownership, evidence IDs, quotations, PDF pages, offsets, document identity, and held-out isolation.
6. Keep human `VerificationResult` separate from `AutomatedSemanticAssessment`; do not claim mocked Agent 2 success is live evidence.
7. Keep explicit cloud consent, sequential calls, no automatic retry/fallback, and the shared eight-call server-side budget.
8. Do not reset SQLite, downgrade migrations, delete user jobs, replace the PDF pipeline, or reintroduce solved downloader/stale-worker bugs.

Already implemented: discovery/source recovery, PDF evidence extraction, finalized `analysis_selected` papers, autonomous Agent 1 handoff, automated semantic assessment model/runtime, separate automated provisional-gap foundation/runtime, job-state projection, five-stage UI, and PDF export. Real Gemini Claim Extraction V3 has persisted a source-grounded claim. Full two-paper Agent 2 execution remains mock-tested rather than live-verified.

## Recommended first task

**Audit and validate the current live five-stage research flow using existing implementation, identify the actual bottleneck preventing two supported analyzed papers from reaching Agent 2, and fix only confirmed issues without weakening eligibility or consuming unapproved Gemini calls.**

Before any live run, inspect the current job state, selected paper IDs, source processing outcomes, ModelRuns/checkpoints, cloud consent, and port-8000 process ownership. Do not use a failure to justify speculative provider/prompt changes.

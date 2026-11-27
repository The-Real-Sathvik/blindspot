# Remaining work for Blindspot

This checklist distinguishes confirmed facts from hypotheses. Read `../BLINDSPOT_HANDOVER.md` and `../AGENTS.md` first.

## P0 — Controlled two-paper autonomous acceptance

- **Current evidence:** Claim Extraction V3 has a real persisted Gemini claim; source recovery and Agent 2 runtime have focused mock coverage. No controlled real run has demonstrated two distinct source-verified papers with current supported automated assessments and a persisted provisional gap.
- **Relevant files:** `backend/app/services/autonomous_research.py`, `claim_extraction_v3.py`, `automated_semantic_assessment.py`, `automated_provisional_gaps.py`, `job_state.py`, `research_report.py`, `backend/app/main.py`.
- **Cause:** not yet verified; insufficient source coverage, provider availability, budget allocation, or a runtime handoff may be the bottleneck. Do not assert one without inspection.
- **Minimal steps:** choose a non-held-out topic with at least two policy-approved sources; verify analysis selection and consent; run once under the eight-call cap; inspect persisted ModelRuns, snapshots, references, and report.
- **Acceptance:** the same two-or-more `analysis_selected` paper IDs appear in evidence, claims, assessments, provisional gap references, API projection, and PDF; all citations are same-job/source-valid; no human review records are created.
- **Tests:** focused autonomous/assessment/gap/report tests before the run; no live retries.
- **Risk/rollback:** cloud quota/provider availability. Stop on failure; preserve records; no prompt/model tuning inside the acceptance run.

## P0 — Source coverage only if the live run exposes it

- **Current evidence:** some historical shortlists contained inaccessible PDFs while recovered alternatives contained usable evidence. Selection finalization was added to avoid reporting those historical candidates as analyzed.
- **Relevant files:** `autonomous_research.py`, `discovery_quality.py`, `web_fulltext.py`, document processing services, `job_state.py`.
- **Cause:** source availability varies by candidate; not automatically a code defect.
- **Minimal steps:** inspect persisted `DocumentProcessingAttempt`, OA metadata, terminal failure status, and existing evidence before changing ranking/recovery.
- **Acceptance:** final analyzed papers have verified evidence; unavailable candidates remain secondary; no repeated terminal downloads or cross-paper substitutions.
- **Risk/rollback:** never broaden URL/publisher access or bypass identity validation merely to increase count.

## P1 — Stabilize the five-stage UI against current backend state

- **Current evidence:** `job_state.project()` is authoritative; stale Uvicorn previously served an old projection. Frontend has compatibility fallback for legacy responses and some hidden legacy/manual code remains.
- **Relevant files:** `frontend/src/main.jsx`, `frontend/src/thread-state.js`, `frontend/src/api.js`, `backend/app/services/job_state.py`, `frontend/vite.config.js`.
- **Suspected cause:** a current discrepancy must be confirmed by comparing `GET /api/jobs/{id}` with rendering; do not hardcode job-specific statuses.
- **Minimal steps:** identify port owner/command line, restart only verified obsolete Blindspot processes when appropriate, then make rendering consume backend `workflow` unchanged.
- **Acceptance:** exactly five stages, limited coverage is displayed honestly, only `analysis_selected` papers are primary, evidence excerpts/read-only findings/gap explanations are readable on desktop/mobile.
- **Tests:** focused frontend state tests and `npm.cmd run build`.
- **Risk/rollback:** retain legacy API handling until proven unused; never hide actual source failure reasons.

## P1 — PDF report polish and content acceptance

- **Current evidence:** `GET /api/jobs/{job_id}/report.pdf` renders a valid PyMuPDF PDF and filters analyzed papers. The renderer is functional rather than fully polished.
- **Relevant files:** `backend/app/services/research_report.py`, `backend/app/main.py`, report tests.
- **Cause:** presentation quality is improvement work, not a confirmed integrity defect.
- **Minimal steps:** only after data-path acceptance, improve layout/page breaks without changing report facts.
- **Acceptance:** valid `%PDF-`; selected papers, exact citations, assessment labels, provisional limitations, and honest insufficient-evidence content render correctly.
- **Risk/rollback:** report must never invent claims/citations/gaps to fill a section.

## P2 — Performance and retrieval improvements

- **Current evidence:** bounded sequential acquisition/inference protects a CPU-constrained Windows laptop and cloud quotas.
- **Relevant files:** discovery/acquisition services and `autonomous_research.py`.
- **Cause:** no measured performance defect is documented here.
- **Minimal steps:** collect safe timings from a controlled run before changing concurrency/retrieval architecture.
- **Acceptance:** measurable improvement with unchanged provenance, budgets, and scientific gates.
- **Risk/rollback:** do not add ChromaDB, LangGraph, Docker, WSL, or new providers without independent approval.

## P2 — Novelty validation

- **Current evidence:** automated gaps are explicitly provisional and not globally validated.
- **Relevant files:** `automated_provisional_gaps.py`, `gap_detection.py`, `research_report.py`.
- **Cause:** a small corpus cannot establish global novelty.
- **Minimal steps:** design a separately approved targeted literature-validation process.
- **Acceptance:** no global novelty claim without independent, traceable evidence.
- **Risk/rollback:** retain the provisional label until that validation exists.

# Blindspot technical handover

## Executive summary

Blindspot is a Windows-native, evidence-grounded research assistant. Its intended primary experience is: a user enters a topic and explicitly consents to cloud research; Blindspot discovers candidate literature, prepares legally available full text, finalizes the papers that actually have verified evidence, extracts claims with Gemini, assesses claim-to-evidence support, proposes only *provisional* cross-paper gaps, and produces a PDF report. It must never invent papers, quotations, pages, claims, or novelty.

The codebase contains the complete autonomous path and a working real Gemini Claim Extraction V3 configuration. Automated semantic assessment and the separate provisional-gap pathway are implemented and mock-tested, but they have **not** yet been demonstrated together in a real multi-paper run. The highest-value next step is one controlled, consented, real end-to-end job that reaches two prepared papers; see [Remaining work](docs/REMAINING_WORK.md).

## A. Product contract

The normal experience is autonomous after topic entry plus explicit cloud consent. The primary UI must not require paper approval, manual upload, human-review buttons, manual claim extraction, or manual gap triggering. Users can inspect read-only evidence and findings and view/export the report.

The visible workflow has exactly five stages:

1. Research topic
2. Paper discovery & full-text preparation
3. Evidence
4. Findings
5. Research gaps

Discovery is a candidate pool, not proof that a paper was analyzed. `ResearchJobPaper.analysis_selected` is the durable selection for the papers actually used downstream. The same selected paper IDs must govern evidence, claim extraction, automated assessment, provisional gaps, and the report. This prevents the prior failure mode where a historical shortlist had inaccessible PDFs while a recovered alternative supplied the real evidence.

## B. Environment and commands

- Root: `D:\Blindspot`.
- Windows-native only; do not introduce WSL, Docker, Linux-only scripts, ChromaDB, or LangGraph without separate approval.
- Python/FastAPI backend: Python 3.12, project interpreter `\.venv\Scripts\python.exe`.
- Frontend: React + Vite (`frontend/`); Vite proxies `/api` to `http://127.0.0.1:8000` in `frontend/vite.config.js`.
- Persistence: SQLite, SQLAlchemy 2.x, Alembic.
- PDF extraction and reporting: PyMuPDF (`fitz`).
- Cloud primary: Gemini. CPU-only laptops are supported; no local model is required by the primary workflow. Ollama remains manual-only.

Start backend (PowerShell):

```powershell
cd D:\Blindspot
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Start frontend:

```powershell
cd D:\Blindspot\frontend
npm.cmd run dev
```

Vite normally serves port `5173`; API is `http://127.0.0.1:8000`. Environment variables are loaded by the backend settings layer; names seen in the project include `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and `BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED`. Keep values only in the ignored root `.env`; never log, commit, or put them in frontend code.

Dependencies are authoritative in `backend/requirements*.txt`, `frontend/package.json`, and lockfiles, not this document.

## C. Architecture map

```text
React/Vite (frontend/src/main.jsx, api.js, thread-state.js)
  -> FastAPI (backend/app/main.py)
    -> autonomous_research.AutonomousResearchRunner
       discovery/OpenAlex/arXiv -> ResearchJobPaper candidates
       web_fulltext + document processing -> SourceDocument -> EvidenceChunk
       finalize analysis_selected papers
       claim_extraction_v3 -> ExtractedClaim + ClaimEvidence + ModelRun
       automated_semantic_assessment -> AutomatedSemanticAssessment + ModelRun
       automated_provisional_gaps -> AutomatedProvisionalGap(+Reference) + ModelRun
       research_report -> Markdown data -> PyMuPDF bytes
    -> job_state.project -> authoritative five-stage response
```

Key locations:

| Concern | Primary files |
| --- | --- |
| App/routes | `backend/app/main.py`, `backend/app/schemas/research.py` |
| Models | `backend/app/models/research.py` |
| Settings/model allowlists | `backend/app/core/config.py`, `backend/app/services/model_config.py` |
| Provider router/adapters/budget | `backend/app/services/cloud_inference.py` |
| Autonomous runner | `backend/app/services/autonomous_research.py` |
| Discovery/ranking | `backend/app/services/openalex.py`, `backend/app/services/discovery_quality.py` |
| Acquisition/extraction | `backend/app/services/web_fulltext.py`, document/evidence services |
| Claim agent | `backend/app/services/claim_extraction_v3.py` |
| Automated assessor | `backend/app/services/automated_semantic_assessment.py` |
| Automated gaps | `backend/app/services/automated_provisional_gaps.py` |
| Human Gap Detection V1 | `backend/app/services/gap_detection.py` |
| Job projection | `backend/app/services/job_state.py` |
| Report/PDF | `backend/app/services/research_report.py` |
| UI | `frontend/src/main.jsx`, `frontend/src/thread-state.js`, `frontend/src/api.js` |

Important API contracts include `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/run` (body contains strict `cloud_consent`), `GET /api/jobs/{job_id}/papers`, `/evidence`, `/claims`, `/gaps`, and `GET /api/jobs/{job_id}/report.pdf`. Legacy/manual endpoints still exist for compatibility, but the primary path is `/run` followed by read-only projections.

## D. Database and migrations

Ownership is job-specific: a `ResearchJob` has `ResearchJobPaper` membership records; papers are global identity records but all usable evidence, claims, runs, and reports are queried through the job association. Never attach a source, claim, or citation to another job merely because the global `Paper` matches.

| Model | Purpose/invariant |
| --- | --- |
| `ResearchJob` | Topic, lifecycle status/error, search/model activity. |
| `ResearchJobPaper` | Job-paper membership, relevance/status/history; `included` is historical shortlist and `analysis_selected` is finalized analysis selection. |
| `Paper` | Canonical bibliographic identity, OpenAlex/arXiv/DOI and OA metadata. |
| `SourceDocument` | Acquired/uploaded document provenance, URL/path/status/hash. |
| `DocumentProcessingAttempt` | Acquisition/extraction outcome, sanitized stage/category/retryability. |
| `EvidenceChunk` | Exact normalized source text with page/offset provenance. |
| `ExtractedClaim` / `ClaimEvidence` | Claim plus exact evidence linkage. |
| `VerificationResult` | **Human** semantic review only; never write it from automation. |
| `ModelRun` | Provider/model/prompt/config hashes, token/latency/error diagnostics and outcome. |
| `AutomatedSemanticAssessment` | Independent AI assessment with immutable evidence snapshot, status, rationale, uncertainty, version and ModelRun. |
| `AutomatedProvisionalGap` / `AutomatedProvisionalGapReference` | Separate automated provisional gaps, deterministic identity and supporting/counterevidence references. |
| `CandidateGap` / references | Existing human-reviewed Gap Detection V1; leave its gate unchanged. |

Assessment statuses are `supported`, `partially_supported`, `contradicted`, and `insufficient_evidence`. Gap status is explicitly provisional/not globally validated. Exact quotation matching is a structural prerequisite, not semantic proof.

Alembic revisions currently present, in order: `20261123_0001` through `20261125_0010`, then `20261126_0011_abstract_evidence`, `0012_automated_semantic_assessments`, `0013_automated_provisional_gaps`, `0014_analysis_paper_selection`, and `0015_provisional_gap_timestamps`. `0015` is an additive correction for timestamps omitted from the provisional-gap migration. Do not downgrade/delete existing user data. Verify the local database revision before a new migration.

Idempotency comes from per-stage terminal outcomes, deterministic evidence/gap identities, current snapshot checks, and ModelRun/checkpoint inspection. Provider calls must occur outside an active SQLite write transaction.

## E. Discovery, source recovery, and final selection

OpenAlex/arXiv metadata is used for candidate retrieval, identifiers, OA locations, ranking, and permitted source URLs. The downloader accepts only approved, HTTPS, policy-validated sources; it enforces redirects/SSRF restrictions, size/time/page/PDF signature checks, safe filenames, and serialized processing. It does not bypass paywalls or invent PDF URLs. `web_fulltext.py` and document processing persist sanitized failures rather than pretending success.

The autonomous runner ranks candidates by relevance and readiness, reuses already verified evidence, processes eligible sources sequentially within bounded limits, and considers relevant job-associated alternatives when the historical shortlist is unusable. It must not repeat terminal failures automatically. Once evidence exists, it finalizes at most the configured number of best relevant source-verified papers by setting `analysis_selected`; unavailable/rejected candidates remain discovery history/technical detail only.

`job_state.synchronize_finalized_analysis_set()` also gives legacy jobs a truthful derived selection from persisted evidence. Treat this as compatibility behavior, not permission to cross-associate evidence.

## F. Agent 1: Claim Extraction V3

The working Gemini baseline is deliberately narrow:

- provider/model: Gemini `gemini-3.5-flash-lite`
- thinking: `MINIMAL`
- maximum output: `512`
- response mode: Gemini `plain_text`; no native response schema for this path
- no automatic retry, fallback, model switch, or paid escalation

`claim_extraction_v3.py` deterministically selects sentence-aligned `EvidenceSpan`s from verified chunks. Python, not the model, derives evidence IDs, source quotation, PDF page, and original offsets. The model gets a bounded packet of supplied evidence IDs and must return exactly:

```text
CLAIM: <one concise supported claim>
EVIDENCE_ID: <one supplied ID>
```

or exactly `ABSTAIN`. `parse_gemini_plain_text_response()` rejects commentary, duplicate/missing/unknown IDs, multiple claims, and malformed output, then converts accepted output to the existing strict V3 schema. Local Pydantic checks, source/job/held-out guards, exact evidence-ID resolution, and ModelRun recording remain mandatory. A structurally accepted claim is stored as semantically unverified.

This configuration has produced a real persisted claim. Earlier Gemini native-schema V3 attempts produced HTTP 400, and JSON/multipart experiments had parsing/truncation issues; do not change the established plain-text contract without new evidence.

## G. Automated semantic assessment

`automated_semantic_assessment.py` provides a distinct assessment agent. It only evaluates current, source-verified claims after creating an immutable evidence snapshot. It does **not** infer support from a valid quotation. The compact, strictly parsed response records status, rationale, and uncertainty with provider/model/ModelRun provenance and assessment version/time.

Before persistence it rechecks claim/evidence identity and snapshot freshness. Malformed output or a stale/mismatched source fails closed without an assessment. Automation never creates or modifies `VerificationResult`; human review and automated assessment are intentionally incomparable records. Only current `supported` automated assessments can support the automated provisional-gap eligibility packet; partial/contradicted evidence can be retained as limitations/counterevidence.

Runtime wiring is present and mock-tested; no real automated-assessment run has yet been established as a product acceptance result.

## H. Agent 2: automated provisional gaps

Keep `gap_detection.py` (human-reviewed V1) unchanged. The separate pathway in `automated_provisional_gaps.py` builds a packet exclusively from current same-job, same-analysis-set claims with valid automated assessments. It requires `supported` findings from **two distinct papers**. Stale, invalid, contradicted, insufficient, cross-job, or non-selected citations cannot become supporting evidence. Partial/contradictory records are preserved separately as counterevidence where applicable.

The runtime uses a strict structured response, validates every referenced paper/claim/assessment/evidence ID against the packet, and persists only `AutomatedProvisionalGap` plus its references. Every output is labeled **“Provisional — Not globally validated.”** It must never assert global novelty. With fewer than two eligible papers, it records/returns insufficient evidence rather than manufacturing a gap.

This is offline/mock verified only. A job with one supported paper correctly produces no automated gap; that is a scientific eligibility result, not a product failure.

## I. Cloud safety, budgets, and failures

Cloud consent is enforced server-side before `/run` can send research content. `main.py` constructs an eight-request job `CloudBudget` with maximum output total 4096, and assignments are bounded to:

- up to 3 claim-extraction calls;
- up to 3 assessment calls;
- up to 2 provisional-gap calls;
- never more than 8 total.

The input admission guard calculates the serialized full request; budgets reserve input bytes and output tokens and fail closed. Provider routing is sequential. There is no automatic fallback/retry/provider switching. Model runs capture sanitized HTTP/finish/usage/hash/error metadata, not credentials or raw sensitive prompts. Transaction-safe boundaries commit/release before generation, then revalidate/persist after generation.

Known provider history: Gemini basic and plain-text V3 extraction have worked; native structured-schema requests previously returned `INVALID_ARGUMENT` 400; one real request saw 503 availability; earlier V2/schema output also hit limits. Groq generation inside Blindspot historically returned 403 despite direct connectivity and is not the primary route. OpenRouter experiments were exploratory and must not silently become a fallback. Ollama is manual-only.

## J. Five-stage frontend and stale-process guidance

`frontend/src/main.jsx` renders the workspace; `frontend/src/api.js` uses relative `/api`; `frontend/src/thread-state.js` maps backend projections to the five labels. `backend/app/services/job_state.py::project()` is authoritative. Frontend-derived fallback calculations should apply only to legacy responses without `workflow`; do not let them override current backend stage status.

Processing and coverage are distinct: a completed stage with limited evidence must say “Finished · Limited coverage,” not “Not ready.” The primary selected-paper display uses `analysis_selected`, while unavailable discovery candidates belong in collapsed secondary detail. Evidence excerpts should be readable with paper/page citation; chunk IDs and offsets are technical detail. Findings must show source linkage separately from automated semantic assessment. Gaps must explain eligibility failure honestly. PDF actions remain readily available.

An old Uvicorn worker previously occupied port 8000 and served outdated job state while current source had newer logic. Safely diagnose before stopping anything:

```powershell
netstat -ano | Select-String ':8000|:5173'
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object ProcessId,CommandLine
```

Only stop a process after its command line proves it is the obsolete Blindspot Uvicorn/Vite process. Do not terminate unrelated processes. Then restart from the commands above and compare `GET /api/jobs/{id}` to what is rendered.

Current UI technical debt: legacy/manual handlers and a hidden old full-text block still exist for compatibility. They should not be exposed in the primary autonomous path, but removing them requires targeted regression coverage.

## K. PDF report

`research_report.py::build_research_report()` builds deterministic report content; `render_research_report_pdf()` renders PyMuPDF PDF bytes. `GET /api/jobs/{job_id}/report.pdf` is the primary export. The report filters its analyzed-paper section by `analysis_selected`, includes evidence coverage, findings/citations, automated assessment statuses, provisional gaps/references when present, limitations, and available DOI/arXiv/source links. A partial job still gets an honest limitations report.

Verify a generated file begins `%PDF-`, can be opened, and contains the expected selected paper IDs/titles and limitations. Never pad it with invented results. Existing rendering is functional but is still a basic academic layout; polish is P1 work.

## L. Current implementation status

| Area | Implemented | Offline/mock tested | Live demonstrated | Important limitation |
| --- | --- | --- | --- | --- |
| Discovery/source recovery/PDF evidence | Yes | Yes | At least one approved arXiv acquisition/extraction | Availability depends on legal sources/network. |
| Final analysis selection | Yes | Yes | Legacy state projection verified | Needs fresh multi-paper autonomous acceptance. |
| Claim Extraction V3 | Yes | Yes | One genuine source-grounded Gemini claim persisted | Single claim does not establish broad quality. |
| Automated assessment | Yes | Yes | Not verified in real autonomous run | Must not be called human review. |
| Automated provisional Agent 2 | Yes | Yes | Not live verified | Needs two distinct supported assessed papers. |
| Five-stage job-state/UI projection | Yes | Focused frontend tests | Current backend response checked after stale-worker correction | Hidden legacy UI remains technical debt. |
| PDF export | Yes | Focused tests | Valid PDF generated from saved job | Visual/report-content polish remains. |

Checkpoint history exists at `c050f7b`, `6748e23`, `7f59f8a`, `e7881b7`, `cde258d`, and `e3ccdca`; `git log` is authoritative for exact changes.

## M. Confirmed lessons

| Historical issue | Current status |
| --- | --- |
| Python 3.12 code accessed nonexistent `HTTPSHandler._check_hostname` | Fixed in `web_fulltext.py`; do not reintroduce. |
| Acquisition failures were opaque | Sanitized processing attempts/logging now distinguish stages; source recovery can continue. |
| Historical shortlist differed from evidence paper | Addressed with `analysis_selected` and finalization/job state/report filtering. |
| SQLite transaction held during Gemini handoff | Corrected with transaction-safe service boundaries. |
| Gemini native JSON/schema and V2 output failures | Avoided by tested plain-text V3 contract. |
| Multipart Gemini candidate handling | Fixed; do not merge different candidates. |
| Human and automated assessment were at risk of conflation | Separate tables/gates now enforce isolation. |
| 0013 lacked timestamp columns | Additive `0015` repairs this. |
| Old server process rendered stale statuses | Diagnose port ownership before assuming code is wrong. |

Current blockers are external/source coverage and real end-to-end validation, not permission to weaken provenance. Historical jobs can legitimately lack automated assessments because they predate runtime integration.

## N. Verified example job

The saved job **“Quantum Computing in Big Data Analytics”** is a legacy evidence example. Recorded state after truthful projection: 27 discovered candidates; 5 original historical shortlist records; 1 finalized analyzed paper; 27 evidence chunks; 1 extracted Gemini claim; 0 automated assessments; 0 provisional gaps. The one analyzed paper is *Quantum Support Vector Machine for Big Data Classification*.

Its expected stages are: topic complete; preparation finished with 1 analyzed paper; evidence finished with 27 verified passages; findings finished/limited with 1 claim not AI-assessed; gaps blocked because the legacy job has no automated assessment. This demonstrates source/evidence/claim provenance and does **not** prove the current automated Agent 2 runtime is broken.

## O. End-to-end behavior

```text
topic + explicit consent
  -> candidate discovery/ranking
  -> permitted source attempt(s), extract exact pages/chunks
  -> finalize verified analysis_selected paper IDs
  -> up to 3 V3 claims (one call/paper)
  -> up to 3 independent assessments (current claims only)
  -> if >=2 distinct supported papers: up to 2 provisional gap calls
  -> persisted status + deterministic PDF
```

- No approved PDF: persist source reason; consider bounded eligible alternatives; finish with limitations if none.
- Only one analyzed/supported paper: claims may be reported; gaps are insufficient cross-paper evidence.
- Claim abstention/failure: record safe terminal outcome; do not fabricate findings or retry automatically.
- Assessment failure: do not mark support; exclude it from Agent 2.
- Invalid gap output: persist no gap; report the validated evidence limitation.
- Budget exhausted: stop provider calls, preserve completed records, produce an honest report.
- Restart: inspect checkpoints/ModelRuns and terminal records; resume only unfinished eligible work.
- Legacy job: project accurately; never backfill fake assessments/gaps.

## P. Tests and validation

Important test locations include `backend/tests/test_autonomous_research.py`, `test_automated_semantic_assessment.py`, `test_gap_detection.py`, `test_web_api.py`, Claim V3/cloud tests, PDF/report tests, and frontend tests under `frontend/src`/configured Vitest paths. Run commands only when authorized:

```powershell
cd D:\Blindspot
.\.venv\Scripts\python.exe -m pytest backend\tests\test_autonomous_research.py backend\tests\test_automated_semantic_assessment.py backend\tests\test_gap_detection.py backend\tests\test_web_api.py
cd D:\Blindspot\frontend
npm.cmd test -- --run
npm.cmd run build
```

Most recently recorded focused results were 27 backend tests passing (with four warnings) before the final frontend projection changes, and 31 frontend tests passing plus a production Vite build after them. Do not misrepresent that as a freshly run full suite. Automated Agent 2 integration is mock-tested. Real Gemini claim extraction and real PDF extraction occurred; real two-paper assessment/gap completion has not.

Recommended acceptance test: create or use a non-held-out topic with at least two legally accessible, identity-valid PDFs; explicitly consent; run once under the eight-call cap; verify exactly the selected same-job papers appear in evidence, claims, assessments, gap references, and PDF. Stop immediately on acquisition/provider failure; do not retry or alter provider parameters during that run.

## Q. Remaining work

See the actionable [remaining-work checklist](docs/REMAINING_WORK.md). In short:

- **P0:** run and observe one controlled real two-paper autonomous job; fix only an observed bottleneck.
- **P1:** finish status/readability polish and remove or isolate legacy manual UI only with regression coverage.
- **P2:** performance, richer retrieval, and separately validated novelty research.

## R. Anti-regression rules

1. Do not change working Gemini V3 plain-text/MINIMAL/512 configuration without evidence.
2. Never bypass document identity, exact evidence IDs/quotes/pages/offsets, held-out checks, or same-job ownership.
3. Do not equate quote validity with semantic support, and never write automated results into `VerificationResult`.
4. Do not permit gaps without two distinct supported analyzed papers; never assert global novelty.
5. Keep the eight-call server-side budget, explicit consent, sequential execution, no retries/fallbacks, and transaction-safe provider calls.
6. Preserve SQLite data and Alembic history; use additive migrations only when necessary.
7. Do not fabricate data to make a UI or PDF look complete.
8. Do not add WSL/Docker or speculative architecture/provider changes while stabilizing the product.

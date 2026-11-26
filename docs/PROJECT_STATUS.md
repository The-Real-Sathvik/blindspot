# Project status

## Current phase

Autonomous research orchestration slice implemented and verified offline. New jobs can launch bounded discovery and permitted full-text preparation automatically; reports remain honest when evidence is insufficient. Live inference, automated semantic assessment, and automated gap generation remain unavailable by design.

Phase 4B.3B.2 — one controlled V2 extraction completed and rejected by strict exact-quotation validation; no annotation has been human-approved.

Phase 4D.2 implementation is complete, but verification is blocked because the configured Python 3.12.4 base interpreter is absent. No cloud request has been made and no annotation has been human-approved.

## Implemented

- Provenance-first SQLite schema with paper metadata, evidence records, and job-paper decisions.
- OpenAlex `/works` discovery with bearer authentication, timeouts, retry handling, local pacing, and in-memory caching.
- OpenAlex metadata normalisation, stable-ID/title-year deduplication, and open-access metadata persistence.
- Deterministic 3–5 query expansion, transparent title/abstract/synonym relevance ranking, and configurable 3–5 paper shortlist selection.
- Source-independent `ScholarlySource` contract plus `paper_external_identifiers` for future adapters without DOI assumptions.
- Search provenance persistence through `search_runs` and `search_results`.
- Additive migration `20261123_0004` for source publication type and separate discovery-metadata completeness fields.
- Separate shortlist-suitability layer with directness labels, heuristic review/survey provenance, tie-level context diversity, and deterministic selection.
- arXiv Atom API client with sequential pacing, bounded retries, namespace-safe parsing, and compact source metadata.
- Additive migration `20261123_0005` for source metadata and validated cross-source match provenance.
- Sequential safe PDF acquisition, PyMuPDF page-level extraction, and deterministic page-linked evidence chunking.
- Local Ollama structured-output client with fixed 4096-token context, bounded output, process-wide sequential lock, and mock-tested error handling.
- Configurable independent model assignments, explicit unload-on-switch lifecycle, and persisted model-run provenance.
- Additive migration `20261124_0006` for successful and failed model-run records without raw prompts or outputs.
- Strict, versioned claim-extraction schema and prompt with deterministic page/chunk quotation validation.
- Additive migration `20261124_0007` for claim-to-model-run provenance, uncertainty records, and validated extraction outcomes.
- Mocked claim-extraction orchestration that records successful abstention, partial validation errors, and failed inference without asserting scientific support.
- A reproducible, separate reference-annotation evaluation harness with distinct structural, provenance, numerical, abstention, and failure metrics.
- Configurable model-candidate registry retaining the verified Llama 3.1 8B Q4_K_M baseline and refusing unverified Qwen3 4B quantization.
- A 27-case, four-paper draft reference set with exact SQLite-validated provenance and a paper-level held-out subset.
- Local Markdown and JSON human-review package with explicit review decisions, reviewer provenance, dataset-version binding, and held-out safeguards.
- Separate `phase-4b-3a-v2` correction dataset, field-level changelog, page-relative quotation coordinates, and isolated v2 review package.
- Separate non-approving quality-review report with independent deterministic source checks and transparent heuristic triage.
- One guarded live Claim Extraction feasibility run with persisted non-sensitive model-run provenance and explicit unload.
- Versioned `claim-extraction-v2` contract that removes LLM-generated offsets and derives them by exact selected-chunk matching.
- One controlled V2 live extraction with separate persisted provenance, explicit unload, and no retry.
- Cloud-first structured-inference infrastructure for Gemini and Groq with provider/model allowlisting, bounded local budgets, safe error categories, and no automatic Ollama fallback.

## Verification status

- Native Windows Python 3.12.4 with `D:\Blindspot\.venv`.
- Alembic migrations through `20261124_0007` are applied to `backend/blindspot.db`.
- Full Phase 1 through Phase 4B.3B.2 suite: `50 passed` in 2.73 seconds after the controlled V2 experiment.
- Phase 2B live OpenAlex smoke test used five deterministic queries with six results each, capped at 30 records. It recorded five search runs and 30 search results, created 24 new papers, and selected five shortlist candidates. Six returned works were already present from Phase 2A.
- Phase 2B.1 re-evaluated the cached 30-record job without an API request. The five selected papers remained members of the shortlist; ordering and reasons now distinguish unknown roles, review/survey hints, unverified metadata hints, and metadata availability.
- Phase 2C live arXiv query retrieved eight records. All eight were new in this bounded sample; cached OpenAlex metadata was attached locally to form a combined shortlist without another OpenAlex request.
- Phase 3 live demonstration downloaded and processed one public arXiv PDF: 20 pages, 50,972 extracted characters, and 55 page-linked evidence chunks.
- Ollama 0.40.16 and `llama3.1:8b-instruct-q4_K_M` are installed locally; actual metadata confirms Llama 3.1 8.0B and Q4_K_M. The live benchmark was not run because only 4,261.4 MB physical RAM was available before inference.
- Phase 4A.2 implemented no live inference, no model download, and no agent logic.
- Phase 4B.1 added no live inference or semantic verification. It has manually reviewable fixtures for numerical findings, contradictory statements, abstention, and fabricated quotations; strict schemas and deterministic checks decide only whether a claim is structurally and location-valid.
- Phase 4B.2 checked all four reference annotations against the stored local document and SQLite offsets. The present corpus supports authentic numerical-result, objective, limitation, and abstention cases. It contains no explicit future-work statement or genuine contradictory finding, so those categories are documented as coverage gaps rather than fabricated. A live sample was not run: 5,338.1 MB available RAM was below the 6,000 MB safety floor.
- Phase 4B.3A sequentially acquired and extracted three additional already-discovered arXiv PDFs through the existing size-capped pipeline: *Fake News Detection with Heterogeneous Transformer* (11 pages, 56 chunks), *Transformer based Automatic COVID-19 Fake News Detection System* (12 pages, 31 chunks), and *Mining Disinformation and Fake News* (18 pages, 56 chunks). Together with the existing visual-content paper, all 27 draft annotations passed source/document/page/chunk/quotation-offset validation. The split is 20 development and 7 held-out cases; all held-out cases come from the mining-disinformation document.
- Phase 4B.3A.1 generated `data/reports/phase_4b_3a_1_annotation_review/REVIEW_PACKET.md` and `review_status.json`. All 27 decisions are `not_yet_reviewed`; approved: 0, needs correction: 0, rejected: 0. The package carries the dataset version and content hash so future review decisions cannot be applied to a changed reference set silently.
- Phase 4B.3A.3 created `phase-4b-3a-v2` without overwriting v1: 22 annotations received substantive scientific corrections, one unchanged scientific label received page-coordinate/numerical-clarity metadata, and four abstention labels remain unchanged. V2 provenance validation passed 27/27; its review package has 27 `not_yet_reviewed` decisions. The 20/7 development-held-out split remains unchanged.
- Ollama executable verification: no executable exists at `D:\AI\Ollama`; the installed executable remains `C:\Users\Sathvik Karumoju\AppData\Local\Programs\Ollama\ollama.exe` (0.40.17). The relocated store at `D:\AI\Ollama\models` contains 26 files (10.42 GB), including Llama 3.1 8.0B Q4_K_M, Qwen3 8.2B Q4_K_M, and Nomic Embed Text 137M F16 manifests/blobs. The user `OLLAMA_MODELS` setting was already correct; a stale GUI/server process inherited an empty old path. Only those related processes were restarted and the server was launched with the D: store. `ollama list` now discovers `llama3.1:8b-instruct-q4_K_M`, `qwen3:8b`, and `nomic-embed-text:latest`; no model inference or download occurred.
- The non-inference v2 quality review found 20 likely-supported records, 3 possibly-incomplete records, 4 cases needing human review, and no deterministic/provenance failures. It made no approval decisions and did not expose held-out labels to prompts or tuning.
- Phase 4B.3B chose development-only `covid-dataset-size`, excluding all seven held-out and seven flagged cases. The existing 6,000 MB gate passed immediately before inference. The one Llama 3.1 8B Q4_K_M request produced a strict-schema-valid response with one candidate, but deterministic quotation-offset validation rejected it. Model run `54c8bb9a-ba1c-4da3-bcd1-9179307cbc5d` is execution-success/`partial_error`; it persisted no claims or evidence. Runtime was 65.159 seconds, output was 195 tokens at 4.788 tokens/second, memory was 6,413.5 MB before, 1,183.6 MB minimum, and 6,688.4 MB after explicit unload. `ollama ps` confirmed no loaded model afterward.
- Phase 4B.3B.1 diagnosed the rejected v1 candidate: the old prompt required the model to calculate four offsets, while raw output is intentionally unavailable, so its quotation correctness remains unknown. V2 removes those fields from model output, performs exact selected-chunk matching in Python, derives both offset coordinate systems, and rejects missing, repeated, cross-boundary, or unexpected-chunk quotations. It introduces no database migration and preserves v1 run history.
- Phase 4B.3B.2 ran one V2 request on the same non-flagged development chunk, excluding all held-out and flagged records. Model run `2e8fff63-d8cf-407e-b62a-23f89dc5d8e3` is execution-success/`partial_error`: one strict-schema candidate was rejected with `quotation_not_found_in_selected_chunk`; zero claims or evidence were persisted. Runtime was 42.517 seconds, output was 85 tokens at 5.173 tokens/second, memory was 6,688.5 MB before, 1,668.2 MB minimum, and 7,509.6 MB after explicit unload. `ollama ps` confirmed no loaded model afterward.

## Not implemented

- arXiv or Semantic Scholar retrieval.
- PDF downloading, extraction, or full-text analysis.
- Live claim extraction, semantic verification, gap detection, ChromaDB, LangGraph, frontend, and background workers.

## Known limitations

- Ranking is deterministic lexical concept coverage, not semantic relevance or study-quality assessment.
- The response cache is process-local and expires after five minutes.
- OpenAlex metadata and abstracts are discovery records, not full-text evidence.
- Publication role hints, method hints, and context hints are screening metadata only; they are not verified claims about study design, datasets, or findings.
- A title-and-author potential duplicate is flagged ambiguous rather than merged; this can leave true preprint/publication pairs separate until stable metadata supports a relationship.
- Multi-column reading order remains dependent on the source PDF text layer; scanned PDFs are recorded as extraction failures because OCR is deliberately out of scope.
- CPU-only Llama 3.1 8B inference needs more headroom than was available during Phase 4A. No runtime, token-rate, JSON, or quotation result is claimed until a safe benchmark runs.
- Phase 4B.1 validates source location and literal quotation only. It does not resolve contradictions, assess semantic support, or establish scientific correctness; those require separately approved independent verification and human review.
- The initial evaluation set is intentionally small and comes from one local document. Its reference-aligned precision/recall is not a substitute for expert semantic judgement, cross-domain validation, or a model comparison.
- The expanded set is still small, all labels are draft, and it does not contain a genuine contradictory-finding pair. Automated checks establish only stored-text and metadata consistency; human review is required before treating labels as scientific reference standards.
- The generated review packet is a local review aid, not evidence of reviewer agreement. A reviewer must inspect source context and record an explicit decision before any label is treated as reviewed.
- V2 remains draft, with no human approvals. Table association for the ensemble numerical result is based on the extracted table's header and row layout and remains subject to human review. Ollama model discovery now uses the relocated D: store, but no Blindspot inference benchmark has been run.
- The quality-review triage is rule-based, not a semantic-review agent or human validation. Its “likely supported” label means only that available source text and deterministic structure are consistent; it does not establish scientific accuracy.
- The raw live response is deliberately unavailable because the provenance model stores only its hash; it must not be reconstructed or recovered by rerunning the experiment. The strict schema result alone does not establish evidence support. The 1,183.6 MB minimum available RAM is too little headroom for repeated CPU-only inference without further review.
- V2 can establish only literal quotation location, not semantic support. Repeated exact text is intentionally rejected until a trusted disambiguation mechanism is separately approved. Future debugging should use an opt-in ephemeral hash/schema/rejection summary rather than persist raw outputs or source passages.
- V2 removed the V1 offset-calculation rejection category but did not yield an accepted candidate in one sample. The raw candidate is unavailable by design, so its claim/category/numerical fidelity cannot be inferred from the rejection. No acceptance-rate or quality comparison can be made from one V1 and one V2 run.

## Next approved task

Stop at Phase 4B.3B.2. Do not run another model inference, select a final model, or proceed to semantic review or Gap Detection Agent work without separate approval.

## Phase 4D.2 verification note

Cloud clients have been implemented but not executed against a live provider. Their normal environment factory intentionally refuses future calls until the developer adds the explicit free-tier attestation `BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED=true` to the local, ignored `.env`; this is a safeguard, not billing verification. The initial Python installation path recorded in `.venv\pyvenv.cfg` no longer exists, so pytest could not run in the existing virtual environment during this phase. Restore the approved Python 3.12.4 installation before running the mocked full suite; do not perform live cloud extraction, semantic review, or Gap Detection inference without separate approval.

## Phase 4D.2.1 cloud input admission

The earlier Python diagnosis was incorrect: sandbox access denied the existing base interpreter. Running the existing virtual environment outside the sandbox reports Python 3.12.4. Both cloud clients now preflight the complete serialized JSON body against configurable local input and combined-context admission limits and account for reserved input bytes alongside requests and output tokens. The full suite passed: `62 passed in 2.74s`. No live API request or database migration occurred. Byte-based admission is a conservative local policy, not a verified provider token count or guarantee about hidden provider framing; provider context and rate limits remain separate. Phase 4D.3 has not been resumed.

## Phase 4D.3 single Gemini extraction

One Gemini `gemini-3.5-flash-lite` V2 request used development chunk `8e85e888-fbe8-423b-b979-360bb174e9c1` on PDF page 3. The 2,884-byte request passed local admission. Model run `10bd7bca-5091-4c58-b9c4-24103d757565` records `failed_inference` and `CloudResponseError`: the returned content did not pass JSON/schema validation. Zero claims or evidence were persisted; the raw response and provider usage are unavailable. The request elapsed 2.099 seconds. No retry, fallback, or scientific verification occurred. The full suite passed: `62 passed in 1.48s`.

## Phase 4D.3.1 response diagnostics

Future cloud runs capture sanitized response classifications, allowlisted finish reasons, provider usage when available, parse/schema outcomes, and raw HTTP body hashes in existing model-run provenance. The earlier run is unchanged and its failure cannot be reconstructed. The 256-token output limit is unchanged and may be restrictive for multiple quotations. No inference occurred in this phase; the full suite passed `75 passed in 1.68s`. No migration was added.

## Phase 4D.3.2 controlled Gemini extraction

One Gemini V2 request on development chunk `8e85e888-fbe8-423b-b979-360bb174e9c1` returned `MAX_TOKENS` and was classified `output_truncated`; no JSON/schema or evidence validation occurred and no claims were persisted. Run `73e083e3-09f6-4581-8b1e-c1bec909a24f` records sanitized diagnostics: 369 reported input tokens, 240 output tokens, and a raw response hash. The measured request elapsed 1.805 seconds. The request body was 2,884 bytes, and the configured output reservation was 256 tokens. No retry or fallback occurred. Full suite: `75 passed in 1.58s`.

## Phase 4D.3.3 bounded output retest

One Gemini V2 request reused the development chunk with a temporary 512-token output reservation. The 2,884-byte body passed the 3,584-byte local body ceiling. Run `44456fcc-2675-4653-bf92-ecda379b1354` records `output_truncated` with provider finish reason `MAX_TOKENS`, 369 input tokens, 494 candidate output tokens, and 1.796 seconds elapsed. JSON/schema and evidence were not checked; no claims were persisted. No retry, fallback, reference change or migration occurred. Defaults remain unchanged. Full suite: `75 passed in 1.59s`.

## Phase 4D.3.4 Gemini thinking diagnostics

The allowlisted Gemini model now receives explicit configurable `MINIMAL` thinking by default. Gemini usage diagnostics distinguish reported prompt, candidate, thinking and total tokens and leave absent fields absent. The provider's documented default was already minimal, and prior runs did not preserve thinking-token counts, so their truncation cannot be attributed to thinking. Output defaults and limits remain unchanged; no inference or migration occurred. Full suite: `77 passed in 1.90s`.

## Phase 4D.3.5 final controlled Gemini V2 retest

One Gemini V2 request with explicit `MINIMAL` and temporary 512-token reservation again returned `MAX_TOKENS`, yielding `output_truncated`; no JSON/schema or evidence validation occurred, and no claims were persisted. The complete request was 2,932 bytes against a 3,584-byte effective body limit. Model run `20562849-49eb-4cd0-aaaf-6afb8610e5d9` records 369 prompt, 496 candidate and 865 total tokens; thinking tokens were not reported. Elapsed time was 1.985 seconds. No retry, fallback, migration or reference change occurred. Full suite: `77 passed in 1.68s`. Stop further Gemini tests of this V2 configuration; recommend a separately approved Groq evaluation.

## Phase 4D.4 controlled Groq claim extraction

One Groq `openai/gpt-oss-120b` V2 request on the same development chunk failed with `CloudAuthenticationError`; the existing client does not distinguish HTTP 401 from 403. Run `dc245597-f369-4e4d-b813-6d0e8cf5eea6` records `failed_inference`; no claims were persisted, and output/schema/evidence validation was not performed. The request measured 2,934 bytes under the 3,584-byte ceiling, reserved 512 output tokens, and took 0.255 seconds. No retry, fallback, migration or reference change occurred. Full suite: `77 passed in 2.10s`.

## Phase 4D.4.1 Groq authentication diagnostics

The diagnostic process loaded the root `.env` key with no process-level Groq override, and its mocked Bearer header matched that key. The prior process's actual key source and HTTP 401/403 status were not retained, so the prior failure remains unexplained. Future HTTP 401 and 403 failures are recorded separately as `authentication_invalid` and `permission_denied`; malformed keys are rejected without revealing them. No inference, database migration or change to the prior run occurred. Full suite: `87 passed in 1.68s`.

## Phase 4D.4.2 controlled Groq retest

One Groq V2 generation request on the development chunk returned HTTP 403, classified `permission_denied`. Run `7f3f51cf-330d-4dd0-84f2-79f23e87d8f5` records `failed_inference`; no output/schema/evidence validation was performed and no claims were persisted. The request was 2,934 bytes against a 3,584-byte effective body limit, with a 512-token output reservation and 0.226-second elapsed time. The exact cause of the 403 is unknown. No retry, fallback, migration or reference change occurred. Full suite: `87 passed in 1.68s`.

## Phase 4D.4.3 Groq HTTP diagnostics

Future cloud HTTP failures preserve numeric status and allowlisted provider error codes from bounded error bodies without retaining raw bodies, messages, headers or credentials. Mocked tests cover 400, 401, 403, 422 and malformed or oversized bodies. The prior 403 and the earlier successful direct request cannot be reconstructed from existing records; the cause remains unknown. No live inference or migration occurred. Full suite: `94 passed in 2.64s`.

## Milestone 1: Claim Extraction V3

V3 deterministically maps evidence IDs to exact chunk substrings and page coordinates, accepts only supplied IDs, and persists valid claim-evidence links without semantic verification. Its development-only workflow verifies job-paper membership, requires a trusted development allowlist, records model-run outcomes, serializes duplicate detection with a SQLite write lock, and avoids duplicate accepted claims on partial retries. The CLI supports explicit cloud execution or rollback-only mock abstention. A real stored development association was exercised in mock mode and rolled back; the prior COVID development chunk passed mocked Gemini input admission. No live provider request, reference change, dependency or migration occurred. Full suite: `100 passed in 2.13s`. Scientific claim accuracy remains unverified; earlier Gemini truncation and Groq HTTP 403 remain unresolved.

## Milestone 2: provisional gap detection

The mocked gap agent uses the `gap-detection-v1` schema and bounded evidence packets. It requires supported claims from at least two papers; contradicted and partially supported findings remain explicitly labeled context. Candidate references are checked against the packet, including counterevidence roles, and all stored gaps are `not_yet_validated`. Migration `20261125_0008` was upgraded and downgraded on an isolated test database and applied to the project database. SQLite integrity was `ok`, foreign-key violations were zero, and existing counts were unchanged: 5 jobs, 42 papers, 198 chunks, 0 extracted claims, 0 claim-evidence rows and 8 model runs. The synthetic in-memory demo returned one provisional candidate. No live inference was run. Full suite: `107 passed, 3 Alembic deprecation warnings in 3.01s`. With no extracted claims in the project database, a real job currently has insufficient evidence for gap inference. The lexical novelty guard, source status and mocked tests cannot establish scientific accuracy or global novelty.

## Phase 5A web workspace

The local FastAPI and React/Vite workspace supports job creation and browsing, explicit bounded OpenAlex discovery, and read-only paper, evidence, claim and candidate-gap views. Agent execution is blocked and no model runs on page load. PDF acquisition and extraction controls are not exposed. No live LLM inference, database reset or new migration occurred. Final verification: `112 passed` in 5.25 seconds (four existing/dependency deprecation warnings), two frontend tests passed, and the Vite production build succeeded. Temporary local API and Vite processes returned a successful health response through the proxy and were stopped after the check. The API is designed for loopback use, not multi-user deployment.

Start the backend from `D:\Blindspot\backend` with `..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`. In a second PowerShell terminal, start the UI from `D:\Blindspot\frontend` with `npm.cmd run dev`, then open `http://127.0.0.1:5173`.

## Phase 5B.1 web document processing

The paper inspector now displays source availability and separate acquisition/extraction controls. The API checks job-paper membership, processes one document at a time, and exposes page-linked evidence through the existing evidence route. Web acquisition is deliberately restricted to stored HTTPS arXiv PDF links with pre-redirect validation; other publishers are marked unsupported. Existing local PDFs/chunks are reused. A synthetic two-page PDF completed acquisition, extraction and repeated-request checks through the API without network access or model inference. Read-only inspection of current job-paper associations found 6 available for acquisition and 6 with extracted evidence; counts are associations, not distinct papers. Full suite: `120 passed` in 6.49 seconds with four existing/dependency warnings; frontend tests: `3 passed`; production build succeeded. No migration or live PDF download occurred in this phase. The process lock is single-worker only, and browser timeouts do not cancel in-progress server processing.

## Phase 5B.2 claim extraction controls

The Evidence Explorer now offers explicit provider selection and per-request consent for experimental V3 extraction on one eligible chunk. The backend verifies job and paper relationships, excludes the held-out document partition, requires a processed local PDF and rechecks normalized source text/page offsets before inference. It uses the existing V3 model-run, evidence ID validation and single-request budget; no automatic provider fallback or gap detection occurs. Mocked API tests covered valid claims, invalid IDs, abstention, reuse, provider/quota errors, consent, wrong jobs, held-out documents and PDF mismatch. Full suite: `127 passed` in 8.64 seconds with four existing/dependency warnings; frontend: `6 passed`; production build succeeded. No live inference, migration or reference-data change occurred. Gemini V2 output truncation and Groq HTTP 403 remain prior observed blockers; V3 live performance is unverified.

## Phase 5A.1 continuous research workspace

The React frontend now displays each saved research job as one ordered idea-to-gap workspace, with sidebar job selection, explicit continuation controls, persisted selection preference and a local-only profile menu with disabled logout. Existing discovery, document processing and consent-gated claim controls remain explicit actions; gap execution remains unavailable. Job-scoped results are cleared on selection and stale responses are ignored. Python suite: `127 passed` in 8.87 seconds with four existing/dependency warnings; frontend: `11 passed`; production Vite build succeeded. No live provider call, migration or new authentication was added. The current API lists at most 100 jobs, and live cloud extraction remains unverified.

## Phase 5B.3 shortlist evidence preparation

The frontend now presents shortlisted papers first, selects a processable shortlist paper by default, displays backend-derived full-text statuses, and offers an explicit sequential preparation action for up to five shortlist entries. Other discovered papers remain individually inspectable. Existing evidence is reused, per-paper failures do not roll back other papers, and browser timeouts warn that server work can continue. Stage states account for partial evidence and claim abstention. Mocked tests cover mixed availability, failure continuation, bounded processing and repeat behavior. No LLM inference, live PDF download, ranking change, migration or database reset occurred. Full suite: `130 passed` in 18.10 seconds with four existing/dependency warnings; frontend: `13 passed`; Vite production build succeeded.
# Phase 5B.4: full-text availability

The research workspace displays reported OA status, supported acquisition source, processing state and evidence separately from relevance, and offers eligible discovered alternatives without changing the shortlist. It supports explicit local PDF uploads with unverified paper identity and existing evidence extraction. Automatic acquisition is limited to stored trusted arXiv, PubMed Central and PLOS PDF endpoints; many legitimate sources remain unsupported. No live AI inference, new scholarly API or schema migration is part of this phase.

Verification: 138 backend tests and 15 frontend tests passed; the Vite production build passed. Download and upload integration tests use generated PDFs and mocked network responses; real publisher and repository downloads have not been verified in this phase.

# Phase 5B.5: real PDF verification and upload identity review

An existing development-job arXiv PDF, “Exploring the Role of Visual Content in Fake News Detection,” was reused without downloading: its first-page title matches the paper record, its 20 pages yield 55 stored chunks, and all 55 chunk/page offset checks passed. Repeated processing reused the same document and chunks. The local evidence API exposes this non-held-out document as eligible for Claim Extraction V3; no inference was performed. Migration `20261125_0009` adds human-confirmed upload association provenance. Uploaded documents still require an exact title match, no detected metadata conflict, and explicit human confirmation; identity is not independently verified.

Final verification: 145 backend tests passed, 16 frontend tests passed, and the production frontend build succeeded. SQLite integrity check passed, foreign keys were enabled, and no foreign-key violations were found.

# Phase 5C.1: first live Gemini Claim Extraction V3

On the development paper “Exploring the Role of Visual Content in Fake News Detection,” one request was made for page 4, chunk `9d7b3eb3-2ae9-4f19-8b92-d3186a0e17ef`. The stored PDF and source offsets passed the existing guard; the chunk has one complete interior evidence span and a truncated opening edge. The request was 2,755 serialized bytes under the 3,584-byte local ceiling, with a 512-token output reservation and one-request budget. Gemini `gemini-3.5-flash-lite` returned HTTP 400 (`invalid_request`) after 1.312 seconds. Model run `86e2d744-6fff-40d7-830f-0c3d0957afeb` is recorded as failed; no structured response, token usage, candidate claims, or evidence validation were available. No claims were persisted. The provider error body was not retained, so the underlying cause remains unknown. No retry or other provider inference was performed.

Post-experiment verification: 145 backend tests and 16 frontend tests passed; the Vite production build succeeded.

# Phase 5C.1.1: Gemini V3 offline diagnosis

The V3 request was compared with the prior V2 request and Google's documented REST and structured-output contracts. Both use the same Gemini generation configuration; V3 changes the candidate schema from a quote and chunk ID to a bounded evidence-ID array. Google documents that `minLength` is ignored by its constrained decoder, while Blindspot's local Pydantic validation still enforces it; Google says this keyword is stripped, not rejected. No incompatibility explaining the prior 400 was established, and the successful direct PowerShell request and 400 response body are unavailable for exact comparison. Future structured errors preserve only a limited allowlist of status, error reason, and known request field paths; raw messages, details, source text and credentials are not recorded. No request schema or prompt was changed and no inference or migration occurred.

Verification: 154 backend tests passed, 16 frontend tests passed, and the Vite production build succeeded.

# Phase 5C.1.2: minimal Gemini structured-output diagnostic

One synthetic Gemini `gemini-3.5-flash-lite` request used a minimal object schema with one required string, `MINIMAL` thinking, a 256-token output reservation and a one-request local budget. The 389-byte serialized request passed the 3,840-byte admission ceiling. The provider returned HTTP 503 with sanitized status `UNAVAILABLE` after 1.445 seconds. No JSON output, finish reason, or token usage was available. This does not establish whether minimal structured output or the V3 schema is accepted. No retry, research extraction, research claim persistence, or migration occurred.

Post-diagnostic checks: 154 backend tests passed and the Vite production build succeeded.

# Phase 6A: research thread and evidence panel

The workspace now presents six compact milestones with a persisted-data progress tracker and contextual next action. Paper details, document processing and identity review, source passages, saved claims and provisional gaps open in a responsive evidence panel. Cloud extraction remains one passage at a time and requires explicit consent; gap execution remains unavailable. Source passages show saved extracted chunks rather than a complete PDF page. Desktop and 390-pixel mobile previews were checked, including workspace switching, Escape, mobile focus trapping and the consent gate; the preview made only read requests. Verification: 154 backend tests passed, 20 frontend tests passed and the Vite production build succeeded. No inference or migration was performed.

# Phase 6A.1: full-text integration and preparation states

The previously observed backend response omitted the `document` field. The current listener on port 8000 was started after the current backend file and its five shortlisted document payloads match a freshly instantiated application; no restart was necessary. The verified "Multi RAG in Educational Institutions" workspace currently has zero papers with extracted evidence, three with eligible PDF links requiring processing, and two requiring PDFs. Preparation displays compact access/processing rows and computes counts from persisted document state. When no shortlisted PDF is processable, the next action opens an eligible discovered alternative or a paper with the local upload option. Missing backend document fields remain explicit rather than being treated as unavailable PDFs. Browser verification used only GET requests; upload identity controls were also checked against a synthetic browser response. Existing development evidence remains accessible. Verification: 154 backend tests and 23 frontend tests passed; the production build succeeded. No inference, PDF download or database migration occurred.

# Phase 6B: one-document preparation attempt

The shortlisted paper "An Empirical Study of Multi-Agent RAG for Real-World University Admissions Counseling" has stored arXiv PDF metadata for `2507.11272`; the source passes the existing HTTPS arXiv endpoint policy and agrees with its stored arXiv DOI. One local backend acquisition request returned a PowerShell `WebException`; its HTTP status and any provider failure detail were not captured. A subsequent read-only status check showed no saved document and the original `available_for_acquisition` state. It is unknown whether an external PDF request was reached. The attempt was not repeated and no other source was tried. Consequently PDF identity, page count, evidence extraction and source offsets could not be verified. The paper panel opens and still offers acquisition; the workspace continues to show zero ready papers and the existing prepare-shortlist next action, reflecting saved state but not preserving this failed client-side attempt. Backend tests: 154 passed; frontend tests: 23 passed; production build passed. No AI inference or migration occurred.

# Phase 6B.1: document-attempt diagnostics

The historical request retained only a client `WebException`; whether localhost, FastAPI, an external connection, validation or persistence was reached remains unknown. A later harmless request for a nonexistent paper did reach localhost and returned 404, but cannot establish the earlier request's path. No historical attempt was fabricated. Additive migration `20261125_0010` stores new backend-accepted job/paper attempts with stage, sanitized category, timestamps, retryability and optional HTTP status. The UI distinguishes local connection and timeout errors, displays persisted source or extraction failures, and directs the user to manual per-paper recovery. Batch preparation skips previously failed attempts. Failed downloads do not create a new SourceDocument, and successful saved documents are reused. No live PDF or AI request occurred.

Verification: 167 backend tests and 25 frontend tests passed; the production frontend build succeeded. SQLite integrity check passed with foreign keys enabled and zero violations. The running backend must be restarted to load the new code.

# Integration recovery sprint

The existing development job and two non-held-out PDFs were exercised through the real API and services in a separate temporary database. Mock extraction, explicitly synthetic test review and mock gap output yielded traceable provisional results; unreviewed or single-paper inputs abstained. The real database remains at 0 claims, 0 reviews and 0 gaps. Human semantic review is available as an explicit self-reported local action, bound to the reviewed claim and evidence snapshot; reviews cannot yet be revised or revoked. Gap inputs require a current human review, intact source and the same research job. This fixes an earlier cross-job eligibility leak and prevents legacy unprovenanced reviews from being treated as supported. The V3 command-line entry point now also invokes the existing PDF/held-out source guard before processing.

Mocked acquisition failure, retry and security checks passed. Gemini and Groq request shapes were inspected with mocked transports only; previous HTTP 400, 403 and 503 underlying causes remain unknown. No inference, PDF download, new migration or model selection occurred. The local backend was restarted with current code and its routes and document state were checked. Final verification: 173 backend tests and 26 frontend tests passed; production build succeeded. The dedicated offline PDF/acquisition run passed 36 tests. SQLite integrity passed, with foreign keys enabled and no violations. See `docs/INTEGRATION_RECOVERY.md` for sanitized request diagnostics, limitations and a separately approved low-usage live acceptance checklist.

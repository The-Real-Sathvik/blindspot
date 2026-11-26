# Architecture

## Implemented scope

Phase 1 establishes the durable provenance model. Phase 2A adds an OpenAlex `/works` adapter for metadata discovery. There is still no PDF processing, LLM call, semantic database, orchestration graph, frontend, or worker process.

## OpenAlex discovery adapter

`OpenAlexClient` loads its key from the project-root `.env` and sends it exclusively in the `Authorization: Bearer` header. It applies a 15-second timeout, a 25-request-per-second local pacing limit, bounded retries (three retries), `Retry-After` handling for `429`, and a five-minute in-memory response cache.

`OpenAlexDiscoveryService` normalises `/works` records, reconstructs inverted-index abstracts, keeps open-access metadata, removes duplicates by OpenAlex ID or DOI, and uses an intentionally conservative same-normalised-title-and-year fallback. It assigns a transparent lexical relevance score based on matched meaningful topic terms and persists new records idempotently through `ResearchJobPaper`.

## System of record

SQLite is the authoritative record. SQLAlchemy models are the persistence layer, Pydantic schemas form the input/output contracts, and Alembic manages database changes. Raw PDFs, extracted text, and reports will be stored locally beneath `data/`, outside Git.

## Autonomous collection slice

`AutonomousResearchOrchestrator` runs the existing discovery and legally permitted document stages sequentially for the bounded shortlist. `SearchRun` and `DocumentProcessingAttempt` remain its restart-safe checkpoints; existing completed work is reused and non-retriable document failures are not retried automatically. It finishes with a deterministic stored-data report even when no full-text evidence is available.

This slice deliberately does not execute cloud claim extraction, automated semantic review, or gap detection. Abstract metadata is counted only as preliminary availability and never enters the page-linked evidence, human-review, or gap-eligibility paths.

## Entity relationships

```text
ResearchJob --< ResearchJobPaper >-- Paper --< SourceDocument --< EvidenceChunk
                                      |                         ^
                                      +--< ExtractedClaim --< ClaimEvidence
                                                           |
                                                           +-- VerificationResult
```

- `ResearchJob` represents a topic-based research session and has a simple processing status and error detail.
- `Paper` stores normalised metadata, stable external identifiers, and an optional link to a related publication/preprint version.
- `ResearchJobPaper` records the paper's inclusion/screening context for a specific job, including failures such as unavailable full text or failed analysis.
- `SourceDocument` represents a full-text source or its retrieval/extraction failure state.
- `EvidenceChunk` preserves page number, section, offsets, ordering, and source text.
- `ExtractedClaim` is linked to its originating paper. A failed claim-analysis attempt is represented on the job-paper processing state rather than inventing a claim row.
- `ClaimEvidence` permits several quoted passages to support one claim. It independently records quotation-existence and source/page-location checks.
- `VerificationResult` stores the semantic support judgement for one claim: supported, partially supported, contradicted, or insufficient evidence.

## Verification separation

## Phase 2B discovery quality

`QueryExpander` produces three to five deterministic, de-duplicated scholarly queries. For fake-news and misinformation topics it uses curated method, platform, and task variants; it never invokes an LLM.

`QualityDiscoveryService` allocates a caller-supplied total retrieval budget evenly over the expanded queries, ranks unique works deterministically, and selects a configurable 3–5 paper prototype shortlist. Its score is relevance evidence, not a quality score: it reports matched topic concepts separately in the title and abstract. Missing abstracts redistribute the abstract weight to title/combined coverage, so a metadata-poor paper is not automatically rejected.

`search_runs` records the source, generated query, timestamp, requested budget, response count, and error state. `search_results` records source rank, source identifier, paper link, relevance/selection decision, and available full-text metadata. `paper_external_identifiers` makes source identifiers extensible for later adapters without requiring a DOI.

## Phase 2B.1 shortlist suitability

The topical relevance score is unchanged. A separate suitability layer labels candidates as direct, adjacent, related, or insufficient from title/abstract concept placement; it never changes a relevance score merely to break ties.

Publication type is stored only when supplied by a source. A title containing `review` or `survey` produces a `review_or_survey_hint` with explicit heuristic provenance; it is not evidence that another paper is experimental. Method and corpus labels are likewise unverified metadata screening hints, not extracted scientific claims.

Within equally suitable topical candidates, selection prefers new disclosed context hints. Review/survey-hinted papers are considered after equally relevant unknown-role records, but are retained when needed to fill a direct-topic shortlist. `has_abstract`, `has_landing_page`, and `metadata_completeness` record availability of three source fields (abstract, landing page, publication type); full-text availability remains independent.

## Phase 2C arXiv discovery

`ArxivClient` uses the public HTTPS Atom API sequentially with a minimum three-second request interval, one request at a time, timeouts, bounded retries, and no PDF acquisition. The Atom parser preserves canonical arXiv IDs separately from versions, dates, categories, DOI, abstract-page URL, and PDF-link metadata.

arXiv metadata is stored compactly on `paper_external_identifiers.source_metadata`. `search_results.match_status` and `match_reason` preserve `new`, stable DOI/arXiv-ID matches, ambiguous title-and-author candidates, and cached records. Only canonical DOI or arXiv identifier matches merge records; title-and-author matches remain separate and are flagged ambiguous. A PDF URL is metadata only, never evidence of a downloaded or verified source document.

## Phase 3 full-text preprocessing

`FullTextService` resolves only stored open-access or arXiv PDF-link metadata, rejects unsafe destinations, streams one PDF at a time under a 20 MB cap, validates redirects and PDF signatures, and generates safe UUID-based local filenames. A URL's availability is distinct from download success.

PyMuPDF extracts text page by page. Deterministic chunking keeps every chunk within one PDF page and stores its page number plus offsets in normalized page text. Empty/scanned or malformed PDFs are marked as explicit extraction failures. No OCR, scientific claim generation, or LLM inference occurs in this stage.

## Phase 4A local Ollama feasibility

`OllamaClient` targets only `llama3.1:8b-instruct-q4_K_M`, fixes `num_ctx` at 4096, limits output to 256 tokens, serializes all inference with a process-wide lock, requests structured JSON using a Pydantic schema, and asks Ollama to unload the model after the response. It has no resource-failure retry loop.

`benchmark_chunk` submits only one existing evidence chunk, records elapsed time, output-token rate when Ollama provides it, available Windows RAM before/during/after, and verifies the returned quotation and chunk ID against the stored evidence text. A benchmark must stop before inference when safe RAM headroom is unavailable.

## Phase 4A.2 multi-model infrastructure

`BlindspotModelConfig` has independent claim-extraction, gap-detection, and optional semantic-review assignments. Each remains fixed at 4096 context tokens but has its own bounded output limit and template version. Assignments can intentionally use the same model.

`SequentialModelRunner` retains the process-wide inference lock and explicitly requests unloading of the prior model before a different assignment runs. `model_runs` records only configuration, hashes, metrics, memory observations, outcome, and safe errors—not prompts, model outputs, or secrets. It is provenance infrastructure only: no agent implementation changes a claim's evidence status.

Citation validity is recorded per `ClaimEvidence`: whether the quoted text exists and whether the cited document/page is valid. Semantic evidence support is recorded separately in `VerificationResult`. A matching quotation is never treated as proof that it supports a claim.

## Phase 4B.1 claim-extraction foundation

`ClaimExtractionService` is a mocked-orchestration foundation only: it does not run a live model in this phase. Its versioned `claim-extraction-v1` prompt requires a strict Pydantic JSON envelope, exact copied quotations, and an explicit empty-list abstention when a passage does not contain an extractable author-stated claim. Unknown or omitted context is retained in `ExtractedClaim.uncertainty_or_missing_information` rather than inferred.

Claim quotation coordinates are zero-based, end-exclusive character offsets in `EvidenceChunk.text`, which is normalized page text. The corresponding page offsets must exactly equal `EvidenceChunk.start_offset + chunk-relative offset`. The validator checks the paper, source document, chunk, page, offsets, and literal substring before it creates `ClaimEvidence`; it rejects an incorrect link rather than repairing it. Repeated text is resolved only by those explicit coordinates. Location and quotation validity remain distinct from semantic support, so valid contradictory source statements can be stored without a `VerificationResult`.

Each extraction records a `ModelRun` and its separate execution `status` and extraction `outcome_status` (`claims_extracted`, `no_claims`, `partial_error`, or `failed_inference`). Partial responses retain only deterministically valid claims and a bounded candidate/accepted/rejected audit summary in the existing safe error fields. No raw response or prompt is stored. The successful idempotency key hashes chunk identity and content, model tag, assignment configuration, and prompt version. On SQLite, the service takes an immediate write lock before reuse lookup and run creation when it owns the transaction, serializing that check-and-create sequence; failed and partial runs are deliberately never reused as successful runs.

## Phase 4B.2 evaluation harness

`claim_extraction_evaluation.py` evaluates only independently observable properties against human-reviewed reference annotations stored separately from agent prompts. An annotation identifies document/chunk/page provenance, expected claim meaning for human review, category, exact quotation, chunk coordinates, uncertainty, and expected abstention. The initial set is grounded in the existing legally acquired local document; its source identifiers and literal offsets were checked against SQLite before use.

Reported metrics remain separate: JSON/schema validity, exact quotation validity, page/chunk/offset correctness, reference-aligned claim precision and recall, numerical-token fidelity, category accuracy, unsupported-reference claim rate, abstention accuracy, and inference failure rate. Reference alignment is an exact provenance-and-quotation comparison, not an assertion of scientific semantic correctness. Nuanced meaning, completeness, contradictions, and interpretation remain human-review tasks.

The evaluation candidate registry preserves the verified Llama 3.1 8B Q4_K_M baseline and permits a `qwen3:4b` entry only after its actual installed quantization is supplied. It never downloads a model or selects a winner. Live evaluation requires one sequential model request, 4096 context tokens, configured bounded output, unload-after-use behavior from the existing client, and at least 6,000 MB available physical memory before inference. The threshold is a conservative safety gate for the 16 GB CPU-only host, not a claim of model quality.

## Phase 4B.3A evaluation dataset expansion

The reference set contains 27 source-grounded annotations from four locally stored, legally acquired documents. It records paper ID, document ID, evidence-chunk ID, PDF page, normalized-text coordinates, literal quotation, expected meaning, category, uncertainty, review status, and split. `draft` explicitly means prepared from the stored source but not approved by a human; there are no `human_reviewed` labels in this set.

`load_reference_annotations`, `coverage_report`, and `validate_reference_provenance` reject duplicate identifiers/locations and report provenance failures instead of repairing them. The held-out split consists of all seven annotations from *Mining Disinformation and Fake News*, avoiding document overlap with the development split. Evaluation annotations are data only and are never interpolated into the generic claim-extraction prompt.

The current corpus has authentic objective, methodology, dataset, result/numerical, limitation, future-direction, research-problem, and bibliography-fragment abstention cases. It has no genuinely contradictory evidence pair, so no contradiction label has been fabricated. Exact location validity is a precondition for scoring, not scientific verification or a substitute for expert review.

## Phase 4B.3A.1 human annotation review

`annotation_review.py` generates a local Markdown review packet and companion JSON status file under `data/reports/phase_4b_3a_1_annotation_review/`. The packet includes the exact label, offsets, numbers, uncertainty, partition, review decision, and a bounded normalized-text context window for every annotation. It does not modify source text, reference labels, or scientific status.

The JSON uses four decision values: `approved`, `needs_correction`, `rejected`, and `not_yet_reviewed`. Any decision other than `not_yet_reviewed` requires a named reviewer and timestamp; correction and rejection also require notes, and correction requires a reason. Only explicit `approved` records become eligible reviewed annotations; rejected records are excluded. A version and SHA-256 hash bind the status file to a particular annotation dataset. Held-out entries are rejected by the development-only guard and must never enter prompts, tuning, or model-selection iterations.

`backend/scripts/validate_annotation_review_package.py` validates a reviewer-edited status file against the immutable annotation identities, dataset version/hash, and current SQLite provenance without changing any decision. The generator is deliberately one-way: it initializes a package and must not be used to overwrite human-entered decisions.

## Phase 4B.3A.3 corrected reference dataset

`claim_extraction_reference_annotations_phase_4b_3a_v2.json` is a separate `phase-4b-3a-v2` dataset derived from, but never overwriting, v1. It preserves all 27 identities, four-paper provenance, and the 20-development/7-held-out partition. It adds page-relative quotation coordinates to non-abstention records and corrects attribution, categories, complete quotations, uncertainty, and numerical interpretation. The companion changelog records each changed field and rationale.

V2 treats `10,700` as one dataset-size quantity. For the COVID ensemble table it records 0.985512 as accuracy and 0.985514 as F1, preserving the table headers and row together as evidence. Source quotation checks validate both chunk-relative and page-relative offsets against the persisted PDF-derived normalized page text. The generic review-package scripts accept explicit dataset, status/output, and version paths, allowing v1 and v2 packages to coexist without overwrite.

## AI-assisted annotation-quality review

`annotation_quality_review.py` is a separate, non-approving review pass. It independently checks the paper/document/chunk association, PDF page, literal quotation, chunk/page offsets, and quoted numerical strings, then applies transparent triage rules. It does not call Ollama or any other model, change annotations, write review decisions, or assert scientific truth.

The v2 report separates `likely_supported` from `possibly_incomplete`, `potentially_misleading`, and `needs_human_review`. The smallest manual-review priority set contains cited-statistic attribution, a table-header/metric association, and two cited-result summaries in a review chapter. Held-out entries are evaluated only as dataset maintenance and are never supplied to Claim Extraction prompts or tuning.

## Phase 4B.3B single controlled live extraction

One development-only passage (`covid-dataset-size`, page 3, chunk `8e85e888-fbe8-423b-b979-360bb174e9c1`) was submitted once to Llama 3.1 8B Q4_K_M through the existing sequential Claim Extraction service. The response passed the strict JSON schema boundary but its one candidate failed deterministic `quotation_offset_mismatch` validation. The run is recorded as execution `success` with extraction outcome `partial_error`; zero `ExtractedClaim` or `ClaimEvidence` rows were persisted and no scientific support status changed.

The model ran for 65.159 seconds, reported 195 output tokens at 4.788 tokens/second, and was explicitly unloaded. Available physical RAM was 6,413.5 MB immediately before the request, dipped to 1,183.6 MB while loaded, and recovered to 6,688.4 MB afterward. This low minimum headroom is a material risk signal; no additional live inference is justified without review. Raw model responses remain intentionally unstored; the provenance record retains hashes, configuration, metrics, outcome, and safe rejection summary.

## Phase 4B.3B.1 quotation-location reliability

`claim-extraction-v2` separates model extraction from deterministic provenance. V2 asks the model only for a claim, category, exact quotation, supplied chunk ID, and uncertainty/abstention. It does not request page number, source-document ID, or offsets. Python confirms the supplied chunk belongs to the expected document and paper, finds the quotation by exact match in that selected chunk only, and derives chunk-relative end-exclusive and page-relative offsets before creating evidence rows.

V1 schemas and prompt behavior remain available for historical provenance, including the rejected v1 live run. V2 rejects a missing quote, unexpected chunk ID, cross-chunk quote, or repeated quotation with no trusted disambiguator. It does not normalize, paraphrase, fuzzy-match, or repair text. An exact quotation is still only a citation-location check, never semantic support.

For privacy-conscious debugging, future active-test diagnostics should remain opt-in and ephemeral: display only response hash, schema outcome, candidate count, rejection codes, and bounded non-source metadata in the terminal. Raw response/source text should not be persisted; any temporary inspection of it requires separate approval and must be discarded at test end.

## Phase 4B.3B.2 controlled V2 live extraction

One V2 request reused the same development-only page-3 dataset-description chunk as the V1 feasibility test. The V2 response passed strict schema validation and contained one candidate, but Python rejected it with `quotation_not_found_in_selected_chunk`; zero claims or evidence rows were persisted. This differs from V1's `quotation_offset_mismatch`, confirming that V2 removed model-calculated coordinates as a failure source, but one rejected sample cannot demonstrate improved extraction acceptance or scientific quality.

The V2 run used Llama 3.1 8B Q4_K_M, 4096 context, and 256 output-token bound. It completed in 42.517 seconds with 85 reported output tokens at 5.173 tokens/second. Memory was 6,688.5 MB before, 1,668.2 MB minimum while loaded, and 7,509.6 MB after explicit unload. The raw candidate remains intentionally unavailable; output hash and safe rejection metadata are retained on model run `2e8fff63-d8cf-407e-b62a-23f89dc5d8e3`.

## Local Ollama location verification

The executable remains `C:\Users\Sathvik Karumoju\AppData\Local\Programs\Ollama\ollama.exe` (0.40.17); no executable exists beneath `D:\AI\Ollama`. The relocated model store is `D:\AI\Ollama\models`. The user-level `OLLAMA_MODELS` is correctly set to that directory. A stale GUI-launched server had inherited the old empty `D:\OllamaModels` value, so only the related Ollama GUI/server processes were restarted and `ollama serve` was launched with the D: store explicitly inherited. `ollama list` then discovered `llama3.1:8b-instruct-q4_K_M`, `qwen3:8b`, and `nomic-embed-text:latest`. No model was loaded for inference or downloaded.

## Phase 4D.2 cloud-first inference integration

`CloudInferenceRouter` provides one structured-output contract for Gemini and Groq and serializes requests with a process-wide lock. It routes only the provider explicitly assigned to an agent; it has no Ollama fallback, quota-based provider switch, retry loop, or automatic model start. `SequentialModelRunner` remains available only when an assignment explicitly selects `ollama`.

Cloud configurations are allowlisted at construction time: Gemini claim extraction uses `gemini-3.5-flash-lite`; the future, unimplemented Gap Detection assignment uses Groq `openai/gpt-oss-120b`. Each assignment retains its 4096-token context, bounded output limit, and versioned prompt identifier. Unknown cloud provider/model pairs are rejected before a request is made.

`GeminiClient` and `GroqClient` request provider-native JSON-schema responses over HTTPS with bounded timeouts and a local request/output-token budget. Authentication, 429/quota, timeout, transport, malformed-response, and schema errors are exposed as safe categories without keys, raw prompts, or raw outputs. Normal application construction uses `CloudInferenceRouter.from_environment()`, which requires `BLINDSPOT_CLOUD_FREE_TIER_CONFIRMED=true` in the untracked root `.env`; the flag is a developer attestation only and cannot establish provider billing state. `ModelRunRecorder` continues to record the provider in the model-configuration JSON alongside the model tag, template version, hashes, metrics, outcomes, and safe errors. Exact evidence matching and semantic support remain distinct.

## Phase 4D.2.1 cloud input admission

Each cloud request is serialized exactly as it will be transmitted, including prompt instructions, evidence, response schema and JSON framing. No separate system or developer messages or tools are currently sent. Before reserving the request or contacting a provider, the client requires the serialized UTF-8 byte count to fit the configured `input_tokens` admission ceiling (default 4096) and that this byte count plus reserved output tokens fit the local 4096 combined-context ceiling. With the default 256 output limit, the effective body limit is 3840 bytes. No text is truncated. Invalid limits, unserializable payloads, and exceeded limits fail closed.

One byte is reserved per serialized byte as a deliberately conservative local admission surrogate, not a provider token count or a mathematically guaranteed upper bound on provider tokenization and hidden framing. Provider model context limits, account RPM/TPM limits, and model-side counting remain separate; the 4096 combined ceiling is a local policy, not a claim about a model's actual context window. A provider can still reject a request or report different usage. No provider tokenizer call is made. Each client instance also reserves serialized input bytes, output tokens and one request against its own in-memory lifetime budget. These counters are not shared across processes, restarts or client instances and do not represent actual provider usage or enforce provider RPM/TPM quotas. Failed dispatched requests remain reserved; rejected preflight requests are not reserved. No retry or fallback occurs.

## Phase 4D.3 single Gemini extraction

One `claim-extraction-v2` request used the development-only COVID dataset-size chunk on PDF page 3. Its full serialized body measured 2,884 bytes and passed the existing local input and combined-context admission checks. The Gemini response failed the client's JSON/schema validation, so no candidate, claim, evidence, or scientific verification was persisted. The privacy-preserving error does not distinguish invalid JSON syntax from schema-invalid JSON. Model run `10bd7bca-5091-4c58-b9c4-24103d757565` records failure, provider, model, prompt version and input reference hash; its raw response is not retained. The externally measured request elapsed time was 2.099 seconds; provider token usage was unavailable after validation failed. No retry or fallback was attempted.

## Phase 4D.3.1 response diagnostics

Cloud response errors distinguish empty or missing content, refusals, truncated output, invalid JSON, invalid structure, missing required fields, invalid field values, unsupported provider envelopes, and unexpected transport failures. Provider finish reasons are accepted only from a fixed allowlist; usage counts are accepted only as bounded nonnegative integers. Where bytes arrive through the real HTTP adapter, a SHA-256 hash of the raw response body is retained; mocked dictionary responses have no raw-body hash. JSON and schema outcomes are separate and do not establish evidence support.

Sanitized response diagnostics are stored in the existing model-run configuration JSON for success and failure, alongside existing provider, model and prompt-version provenance. Raw response and error values are never stored in these diagnostics. The prior failed run remains unchanged. A 256-token output cap may be insufficient for several V2 claims with exact quotations and uncertainty, but no provider tokenizer or retained prior response establishes whether truncation occurred. Output and input limits are unchanged.

## Phase 4D.3.2 controlled Gemini extraction

One Gemini `gemini-3.5-flash-lite` V2 request used the same development-only page-3 chunk, with a 2,884-byte request and a 256-token output reservation. Provider finish reason `MAX_TOKENS` was classified as `output_truncated` before JSON/schema validation; no claims or evidence were persisted. Provider-reported input and output counts were 369 and 240 tokens; measured elapsed time was 1.805 seconds. Model run `73e083e3-09f6-4581-8b1e-c1bec909a24f` preserves sanitized diagnostics and the raw HTTP response hash. The client records response receipt but does not record a numeric HTTP status. No retry, fallback, or scientific verification occurred.

## Phase 4D.3.3 bounded output retest

One Gemini V2 extraction reused the development-only page-3 chunk with a temporary 512-token output reservation; the configured default and Groq assignment were unchanged. The serialized request was 2,884 bytes, below the 3,584-byte local body ceiling. The provider returned `MAX_TOKENS` and 369 input / 494 candidate output tokens. The client classified `output_truncated` before parsing; no candidate, evidence or scientific-support decision was persisted. Model run `44456fcc-2675-4653-bf92-ecda379b1354` retains sanitized diagnostics and response hash; elapsed time was 1.796 seconds. No retry occurred. Thinking settings were unchanged; Gemini documents that output and thinking share the generation limit, but this run's diagnostics did not capture a separate thinking-token count.

## Phase 4D.3.4 Gemini thinking diagnostics

Gemini `generateContent` requests explicitly set `generationConfig.thinkingConfig.thinkingLevel` to `MINIMAL` by default for the allowlisted model, with validated model-assignment configuration and no thinking override on Groq or Ollama. Gemini's documented default for this model is already minimal, so explicitly supplying the setting does not establish a change in model behavior. The generation request continues to use `responseMimeType`, `responseJsonSchema`, and `maxOutputTokens`; its complete body, including thinking configuration, is measured by the existing admission check.

Sanitized Gemini usage diagnostics now separately preserve reported prompt, candidate, thinking and total token counts, when present. Absent fields remain absent, including `thoughtsTokenCount`; candidate counts are not described as total generation usage. No historical responses are modified. The default output reservation remains 256, and a validated 512-token per-assignment override remains available. No live inference was conducted in this phase.

## Phase 4D.3.5 final controlled Gemini V2 retest

One Gemini `gemini-3.5-flash-lite` V2 request used the development page-3 chunk with explicit `MINIMAL` thinking and a temporary 512-token output reservation. The complete 2,932-byte request passed the local 3,584-byte effective body ceiling. It returned `MAX_TOKENS`, classified as `output_truncated`, so JSON/schema and evidence validation did not run and no claims were persisted. Model run `20562849-49eb-4cd0-aaaf-6afb8610e5d9` records sanitized diagnostics: 369 prompt tokens, 496 candidate tokens, 865 total tokens, and no reported thinking-token field. Elapsed time was 1.985 seconds. The missing thinking field is not treated as zero. No retry or fallback occurred. Stop further Gemini experiments with this V2 configuration; evaluate Groq only after separate approval.

## Phase 4D.4 controlled Groq claim extraction

One Groq `openai/gpt-oss-120b` V2 request used the same development-only page-3 chunk with a temporary 512-token output reservation. The 2,934-byte JSON body passed the 3,584-byte local body ceiling. The existing client sent native `json_schema` in best-effort mode, with provider-default reasoning effort and `max_tokens=512`; Groq documents `max_tokens` as deprecated in favor of `max_completion_tokens`. Model run `dc245597-f369-4e4d-b813-6d0e8cf5eea6` records `failed_inference` / `CloudAuthenticationError`. The client groups HTTP 401 and 403 into that category, so the exact status and cause are unknown. No response content was available for validation, no claims were persisted, and no retry or fallback occurred. Elapsed time was 0.255 seconds.

## Phase 4D.4.1 Groq authentication diagnostics

The root `.env` is loaded with `override=False`: pre-existing process environment variables take precedence. In the diagnostic process, no Groq key override was present, the resolved key matched the root file, and a mocked Groq request used the correct Bearer header, endpoint, content type and model. This does not establish the earlier process environment or explain its failure. The client rejects credentials with embedded whitespace, quotes or non-printable/non-ASCII characters without exposing them. Provider 401 and 403 responses now produce the safe model-run error categories `authentication_invalid` and `permission_denied` respectively; the previous record remains unchanged. No network request was sent in this phase.

## Phase 4D.4.2 controlled Groq retest

One Groq `openai/gpt-oss-120b` V2 request reused the development-only page-3 chunk with the root `.env` key, no pre-existing Groq environment override, and a 512-token output reservation. The 2,934-byte body passed the 3,584-byte local admission ceiling. HTTP 403 was classified `permission_denied`; no provider error body, model output, response hash or token usage was retained. Model run `7f3f51cf-330d-4dd0-84f2-79f23e87d8f5` records `failed_inference`; elapsed time was 0.226 seconds. No claims or evidence were persisted. The underlying cause of the HTTP 403 is unknown; listing a model does not establish generation permission. No retry or fallback occurred.

## Phase 4D.4.3 HTTP error diagnostics

Cloud HTTP failures now retain the numeric status and, when present in a bounded JSON error body, a provider error code only if it matches a fixed allowlist. The body, message and headers are discarded; malformed or oversized bodies yield only a status. Sanitized categories distinguish 400, 401, 403 and 422. These fields are recorded in existing model-run configuration JSON, with no migration. The successful prior direct PowerShell generation command and the previous 403 body are unavailable in the repository, so their exact request differences and the 403 cause remain unknown. Groq documents GPT-OSS 120B support for best-effort JSON Schema and may return 400 for schema failures; it documents `max_tokens` as deprecated, not forbidden. No generation was attempted in this phase.

## Milestone 1: Claim Extraction V3

`claim_extraction_v3.py` builds deterministic sentence-aware spans from one stored normalized chunk. Span IDs are SHA-256 hashes of the span algorithm version, document and chunk identity, page, chunk offset, chunk-text hash, and end-exclusive span coordinates. Each span retains the exact stored substring, document, chunk, page, and chunk/page coordinates; first and last spans are marked as potentially incomplete. Segmentation is heuristic and can misinterpret abbreviations or PDF layout. IDs are reproducible from unchanged chunk data and span version; the existing claim-evidence rows persist their resolved quotations and coordinates rather than introducing a new span table.

The V3 schema requires a claim, category, one or more supplied evidence IDs, uncertainty and an explicit claims array for abstention. The provider never calculates offsets or quotes. The workflow rejects unknown/duplicate IDs, records bounded rejection reasons, and persists valid source links separately from scientific support; it never creates a `VerificationResult`. A true quote or ID cannot establish a claim's meaning. V3 does not alter V1 or V2.

`run_development_extraction` requires a fresh SQLite session, a research job linked to the chunk's paper, and an explicit caller-provided trusted development-chunk allowlist. It takes `BEGIN IMMEDIATE` before reading, uses the job/chunk/content/assignment/prompt/span version for successful-run reuse, and prevents duplicate persisted claims on partial retries. Failed and partial runs remain auditable and are not reused as successes. The caller must commit or roll back; the write lock is held until then, including during provider execution. The CLI builds its development allowlist from the v2 annotation partition, excludes documents containing held-out cases, and never inserts reference answers into the prompt. It offers a rollback-only `--mock` mode and an explicitly selected cloud `--execute` mode. Callers supplying their own allowlists are responsible for excluding held-out content. The existing local cloud budgets and no-fallback controls still apply; oversized V3 requests fail rather than truncate evidence.

## Milestone 2: provisional gap detection

`gap_detection.py` constructs a deterministic, size-bounded cross-paper packet from job-linked claims, their source-linked quotations and `VerificationResult` records. Literal quotation and document/paper relationships are rechecked. Unverified and insufficient-evidence claims are excluded; partial and contradicted findings are labeled context rather than established support. Packet selection is round-robin across papers and may omit material when the byte limit is reached. Callers must supply approved chunk IDs and exclude held-out material. The prompt explicitly states that an incomplete packet cannot establish absence in the literature.

The strict `gap-detection-v1` output contract requires a provisional statement, conservative category, supporting and optional counterevidence references, explanation, uncertainty, counterevidence text and `not_yet_validated` status. All identifiers are checked against the packet, and at least two different papers with supported claims are required. Contradicted or partially supported items may be cited as counterevidence but never as supporting claims. A narrow lexical novelty-assertion guard is not a scientific novelty verifier and cannot recognize all unsupported claims. Gap reasoning and the reliability of existing semantic verification labels require independent human review and targeted literature searches.

Additive migration `20261125_0008` creates candidate gaps and paper/claim/evidence reference tables with foreign keys and fixed provisional status. A `ModelRun` records the selected provider and input/output hashes; successful repeated runs are reused. A fresh SQLite transaction takes an immediate write lock, held during any inference. The local cloud client still performs full-request admission; because schema and packet size both count, it can reject an oversized request without truncating evidence. No provider is automatically selected as fallback. `run_gap_detection.py --demo` uses only synthetic in-memory records; explicit cloud execution requires a job and caller-supplied approved chunks.

## Phase 5A local web workspace

`backend/app/main.py` provides a local FastAPI application over the existing SQLite records. It creates and lists jobs, explicitly invokes bounded OpenAlex discovery, and reads papers, source evidence, claims, candidate gaps and stage outcomes. API responses do not contain provider credentials or model configuration. As of Phase 5B.2 claim extraction has a separate explicit, consent-gated route; gap detection remains blocked, and page loads never invoke inference. Write endpoints require a custom local request header, and CORS is restricted to the local Vite origins. The development server must bind to loopback; this is a local workspace rather than a multi-user authentication system.

`frontend/` is a React/Vite client with a local `/api` development proxy. It renders existing research artifacts and clearly separates literal citation checks from semantic support. No synthetic research records are inserted to populate the interface. Phase 5B.1 added explicit PDF acquisition and extraction controls; Phase 5B.2 added experimental, consent-gated one-chunk V3 claim extraction. Gap detection remains unavailable in the UI.

## Phase 5B.1 web document processing

The paper inspector exposes separate, explicit acquire and extract actions for one job-associated paper at a time. The web acquisition path currently accepts only stored HTTPS arXiv `/pdf/` metadata; unsupported publisher links are reported rather than fetched. Redirects are checked against the same host/path policy before being followed. The existing downloader enforces its 20 MB and socket-timeout limits; the web adapter also checks a 45-second deadline between reads. Local extraction accepts only files under the project source-document directory, limits PDFs to 300 pages, and reuses the existing PyMuPDF page/chunk processor. Existing PDFs and chunks are reused without re-downloading or clearing valid evidence. Failed extractions do not retain partial chunks.

An in-process nonblocking lock prevents concurrent document work in the default single-worker server. This is not a distributed lock; do not run multiple API workers for document processing. A browser request times out after 60 seconds, but that does not cancel a server operation already running; a socket read or redirect can extend beyond the between-read deadline. No background job queue or cloud inference is started. The API never accepts a user-provided PDF URL.

## Phase 5B.2 consent-gated claim extraction

`POST /api/jobs/{job_id}/claims/extract` requires a strict boolean `cloud_consent`, a selected `chunk_id`, and an allowlisted `provider` (`gemini` default or explicitly selected `groq`). It processes one chunk, reserves at most one provider request under existing byte/output budgets, does not retry or fall back, and calls the existing V3 workflow. Its process lock is shared with document processing in the single-worker server. Outcomes include accepted and rejected counts, safe rejection categories and ModelRun ID; previous successful identical runs are reused.

Before a provider call, the workflow verifies the job-paper relationship, excludes documents in the v2 held-out partition, requires a locally stored processed PDF, checks the saved content hash when available, and re-extracts the selected PDF page to verify the normalized chunk and page offsets. A missing or inconsistent partition fails closed. The partition manifest is read for source-document IDs only; expected reference answers never enter the prompt. The UI explicitly identifies the provider and model and requires the user to consent to transmitting the selected chunk. Structural ID/quotation checks do not establish semantic support. The free-tier attestation and local budgets do not prove provider billing settings.

## Phase 5A.1 continuous research workspace

The frontend presents one job's idea, discovery, evidence, claims and gaps in ordered sections. The sidebar lists existing `ResearchJob` records; local storage keeps only the selected job ID as an optional navigation preference, never a second copy of research data. Job changes immediately clear the visible result bundle and ignore stale asynchronous responses. Continue controls navigate to sections without starting discovery, document processing or inference.

Stage labels are derived from saved artifacts, saved run status and the current explicit operation; a saved claim is not treated as semantically verified. Gap execution remains disabled, including when supported claims exist, until live integration is approved. The top-bar menu describes local profile/settings and makes logout unavailable because no authenticated session exists. Existing PDF and consent-gated V3 controls are reused unchanged.

## Phase 5B.3 shortlist evidence preparation

Paper discovery displays persisted shortlist entries first and other records under an expandable section. The displayed relevance value is a ranking score, not a calibrated probability; full-text status comes separately from the backend's document state. On initial load or completed discovery, the inspector prefers a shortlisted paper with existing evidence, then a saved PDF, then a supported PDF link. When none is usable, it can select an eligible non-shortlisted paper; if no paper is usable it leaves the inspector unselected. Manual paper selection remains available.

The explicit `POST /api/jobs/{job_id}/prepare-shortlist` action uses only persisted job associations marked shortlisted, in ranking order, capped at five. It holds the existing single-worker document lock, processes eligible papers sequentially through the existing restricted acquire/extract paths, commits each paper separately and returns safe per-paper statuses. A repeated request reuses completed evidence; retryable failures may be retried by a later explicit action. This route never calls an inference provider. The browser refreshes evidence after completion and scrolls to it; a client timeout does not cancel server processing and the UI requires a status refresh before another batch attempt.

Stage presentation distinguishes partial shortlist coverage and successful claim abstention from completion. Technical chunk IDs are available in expandable source details. The HTTPS arXiv-only web acquisition policy, PDF limits, local-source provenance and semantic verification boundaries are unchanged.

## Failure and retry posture

The job, job-paper association, and source document all preserve an error message and status. States include unavailable full text, failed extraction, missing evidence, failed analysis, and retriable error. Pipeline modules will update these records rather than delete failures.

## Resource posture

Future document and Ollama processing will be sequential, bounded by chunk size, and persisted after every stage. Phase 1 loads neither documents nor models.
# Phase 5B.4: bounded full-text sources and uploads

OpenAlex normalization retains a bounded list of explicitly open location-level PDF URLs and source types alongside existing OA metadata. Existing records acquire those fields on rediscovery only when the stable OpenAlex ID matches. Web acquisition accepts stored HTTPS arXiv PDF links, explicitly open PubMed Central `/articles/PMC…/pdf/…` links, and explicitly open PLOS `/…/article/file?...&type=printable` links. OA landing pages are not assumed to be PDFs. Source selection keeps relevance independent of availability. DNS answers must all be public; connections are pinned to a validated IP with TLS hostname verification, proxies are disabled, and redirects are rechecked against the same endpoint allowlist. Existing limits remain 20 MiB, 300 pages and bounded sequential processing. Restrictive endpoint and redirect rules can reject legitimate full texts.

`POST /api/jobs/{job_id}/papers/{paper_id}/upload` accepts a raw `application/pdf` body, checks job association, signature, size and PDF structure/page limits, and stores a SHA-256 digest with a generated filename. Identical saved files are reused; a different saved PDF is not replaced. Upload provenance explicitly records that the paper identity is not independently verified.

# Phase 5B.5: human review of uploaded document associations

The document identity endpoint compares the selected paper with bounded embedded PDF metadata and the first-page title. It accepts an exact normalized meaningful title match only, treats a differing embedded title or explicit embedded DOI as a conflict, and never infers a DOI from the bibliography. Missing title evidence or conflicts block confirmation and cloud extraction. The local user must inspect the metadata and explicitly confirm; a review token binds the action to the displayed metadata and document hash. `upload_identity_reviews` records the document, job, hash, comparison, action and timestamp. A changed hash or paper metadata invalidates the approval. This is a human-confirmed association, not independently verified identity or semantic claim verification. Uploaded copies matching held-out document hashes remain blocked. Alembic revision `20261125_0009` is additive.

# Phase 5C.1: V3 live integration observation

The first controlled Gemini V3 generation request passed the local source and serialized-input admission checks but received HTTP 400 (`invalid_request`). The application recorded failed ModelRun `86e2d744-6fff-40d7-830f-0c3d0957afeb`; no structured candidate or evidence was persisted. The sanitized HTTP status does not establish the cause. V3 structural evidence validation and semantic support remain distinct, and no live V3 success has yet been demonstrated.

# Phase 5C.1.1: offline Gemini request diagnosis

Google documents that `minLength` is ignored during constrained decoding and does not cause rejection; Pydantic enforces it locally. The V3 request and evidence schema remain unchanged because no cause of the recorded 400 can be established offline. Future HTTP diagnostics may record allowlisted provider statuses, recognized reasons, and exact allowlisted request field paths from structured error details. Provider messages, field descriptions, unknown paths and raw error bodies are discarded. The previous ModelRun is unchanged.

# Phase 6A: research thread

The React workspace derives a six-milestone thread and next action from the existing job, paper, document, claim and gap endpoints. A contextual desktop panel becomes a keyboard-accessible mobile sheet for paper details, processing controls, extracted source passages, citations and provisional gaps. It invokes existing endpoints only after explicit user actions; provider consent remains per passage. No additional backend workflow or database schema was introduced.

# Phase 6A.1: preparation state presentation

Compact preparation rows classify persisted document states independently as access availability and local processing progress. Summary counts separate extracted evidence, processable documents, papers needing PDFs, and missing response data. The next action opens a discovered eligible alternative or the selected paper's upload controls when no shortlisted PDF can be processed; missing fields are reported as a backend mismatch, not as a PDF availability determination. Existing API contracts remain unchanged.

# Phase 6B.1: durable document-attempt diagnostics

An additive `document_processing_attempts` table records backend-accepted acquisition and extraction operations, keyed to job and paper. The started record is committed before work begins; a completed failure stores only a fixed stage/code, optional HTTP status, retryability and timestamps. A client connection failure before FastAPI accepts a request has no backend attempt record. DNS, TLS, source HTTP, timeout, policy, PDF validation, extraction and unexpected errors are classified without retaining raw exception text. Failed acquisitions do not create a new source document, while prior successfully saved evidence is preserved. Repeated batch preparation skips failures; individual retries require an explicit user action. An interrupted started attempt is displayed as incomplete rather than claimed successful. SQLite availability is required to persist attempts.

# Integration recovery: human review and gap gate

`POST /api/jobs/{job_id}/claims/{claim_id}/semantic-review` records an explicit human assessment in the existing `VerificationResult`. Its versioned envelope stores a self-reported reviewer, rationale, assessment, timestamp and a hash of the claim and citation provenance. Before saving, source PDF pages and exact quotation coordinates are rechecked. This is a human self-attestation, not authenticated reviewer identity or an automatic semantic verifier. A review cannot yet be replaced or revoked; legacy or stale records are displayed as unverified. Gap packets require a current review envelope, valid citations and PDF checks, and a claim extraction run from the same research job. Two supported papers are required; partial or contradicted findings remain labeled context. No schema migration was needed.

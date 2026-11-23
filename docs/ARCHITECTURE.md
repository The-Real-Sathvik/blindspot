# Architecture

## Implemented scope

Phase 1 establishes the durable provenance model. Phase 2A adds an OpenAlex `/works` adapter for metadata discovery. There is still no PDF processing, LLM call, semantic database, orchestration graph, frontend, or worker process.

## OpenAlex discovery adapter

`OpenAlexClient` loads its key from the project-root `.env` and sends it exclusively in the `Authorization: Bearer` header. It applies a 15-second timeout, a 25-request-per-second local pacing limit, bounded retries (three retries), `Retry-After` handling for `429`, and a five-minute in-memory response cache.

`OpenAlexDiscoveryService` normalises `/works` records, reconstructs inverted-index abstracts, keeps open-access metadata, removes duplicates by OpenAlex ID or DOI, and uses an intentionally conservative same-normalised-title-and-year fallback. It assigns a transparent lexical relevance score based on matched meaningful topic terms and persists new records idempotently through `ResearchJobPaper`.

## System of record

SQLite is the authoritative record. SQLAlchemy models are the persistence layer, Pydantic schemas form the input/output contracts, and Alembic manages database changes. Raw PDFs, extracted text, and reports will be stored locally beneath `data/`, outside Git.

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

Citation validity is recorded per `ClaimEvidence`: whether the quoted text exists and whether the cited document/page is valid. Semantic evidence support is recorded separately in `VerificationResult`. A matching quotation is never treated as proof that it supports a claim.

## Failure and retry posture

The job, job-paper association, and source document all preserve an error message and status. States include unavailable full text, failed extraction, missing evidence, failed analysis, and retriable error. Pipeline modules will update these records rather than delete failures.

## Resource posture

Future document and Ollama processing will be sequential, bounded by chunk size, and persisted after every stage. Phase 1 loads neither documents nor models.

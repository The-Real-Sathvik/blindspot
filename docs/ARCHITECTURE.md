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

Citation validity is recorded per `ClaimEvidence`: whether the quoted text exists and whether the cited document/page is valid. Semantic evidence support is recorded separately in `VerificationResult`. A matching quotation is never treated as proof that it supports a claim.

## Failure and retry posture

The job, job-paper association, and source document all preserve an error message and status. States include unavailable full text, failed extraction, missing evidence, failed analysis, and retriable error. Pipeline modules will update these records rather than delete failures.

## Resource posture

Future document and Ollama processing will be sequential, bounded by chunk size, and persisted after every stage. Phase 1 loads neither documents nor models.

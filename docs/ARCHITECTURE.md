# Architecture

## Phase 1 scope

Phase 1 establishes a durable provenance model only. It has no live API calls, PDF processing, LLM calls, semantic database, orchestration graph, frontend, or worker process.

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

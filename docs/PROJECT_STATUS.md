# Project status

## Current phase

Phase 4A — local Ollama integration, benchmark safely deferred.

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

## Verification status

- Native Windows Python 3.12.4 with `D:\Blindspot\.venv`.
- Alembic migrations through `20261123_0003` are applied to `backend/blindspot.db`.
- Full Phase 1 through Phase 4A mock suite: `28 passed` in 0.53 seconds on the final run.
- Phase 2B live OpenAlex smoke test used five deterministic queries with six results each, capped at 30 records. It recorded five search runs and 30 search results, created 24 new papers, and selected five shortlist candidates. Six returned works were already present from Phase 2A.
- Phase 2B.1 re-evaluated the cached 30-record job without an API request. The five selected papers remained members of the shortlist; ordering and reasons now distinguish unknown roles, review/survey hints, unverified metadata hints, and metadata availability.
- Phase 2C live arXiv query retrieved eight records. All eight were new in this bounded sample; cached OpenAlex metadata was attached locally to form a combined shortlist without another OpenAlex request.
- Phase 3 live demonstration downloaded and processed one public arXiv PDF: 20 pages, 50,972 extracted characters, and 55 page-linked evidence chunks.
- Ollama 0.40.16 and `llama3.1:8b-instruct-q4_K_M` are installed locally; actual metadata confirms Llama 3.1 8.0B and Q4_K_M. The live benchmark was not run because only 4,261.4 MB physical RAM was available before inference.

## Not implemented

- arXiv or Semantic Scholar retrieval.
- PDF downloading, extraction, or full-text analysis.
- Ollama, claim extraction, verification, gap detection, ChromaDB, LangGraph, frontend, and background workers.

## Known limitations

- Ranking is deterministic lexical concept coverage, not semantic relevance or study-quality assessment.
- The response cache is process-local and expires after five minutes.
- OpenAlex metadata and abstracts are discovery records, not full-text evidence.
- Publication role hints, method hints, and context hints are screening metadata only; they are not verified claims about study design, datasets, or findings.
- A title-and-author potential duplicate is flagged ambiguous rather than merged; this can leave true preprint/publication pairs separate until stable metadata supports a relationship.
- Multi-column reading order remains dependent on the source PDF text layer; scanned PDFs are recorded as extraction failures because OCR is deliberately out of scope.
- CPU-only Llama 3.1 8B inference needs more headroom than was available during Phase 4A. No runtime, token-rate, JSON, or quotation result is claimed until a safe benchmark runs.

## Next approved task

Stop at Phase 4A. Do not implement Claim Extraction Agent work until a safe benchmark is reviewed and approved.

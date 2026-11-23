# Evaluation plan

## Research question

Can an evidence-verification-driven research pipeline improve reliability and traceability of automated research-gap discovery compared with simpler LLM-based approaches?

## Planned comparisons

- Baseline A: one LLM supplied only the research topic.
- Baseline B: one LLM supplied the same research evidence available to Blindspot.
- Proposed system: structured extraction, citation checks, semantic verification, and cross-paper reasoning.
- Ablation: proposed system with evidence verification removed.

## Measurements

- Retrieval relevance and deduplication accuracy.
- Structured claim extraction and verification accuracy.
- Unsupported-claim rate.
- Evidence quality and human-reviewed relevance of proposed gaps.
- Runtime and observed memory consumption.

## Method safeguards

Use a fixed, documented evaluation corpus with human annotations and provenance. Measure observed values; do not invent results. Treat survey future-work sections as evidence, not ground-truth gaps. Report uncertainty and counter-evidence.

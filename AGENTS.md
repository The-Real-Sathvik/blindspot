# Blindspot contributor instructions

## Project boundary

Blindspot is a Windows-native, evidence-grounded research-gap discovery system. Work only in this repository. Do not use WSL, Docker, Linux-specific tooling, or background services unless explicitly approved.

## Technical constraints

- Use Python, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic, and SQLite.
- Use PowerShell-compatible commands and native Windows paths.
- Ollama is fixed to Llama 3.1 8B Instruct Q4_K_M unless the user approves a change.
- Run LLM work sequentially. Do not load multiple models or process full papers concurrently.
- Do not add ChromaDB or LangGraph until their need is demonstrated and independently approved.

## Scientific integrity

- Never fabricate papers, citations, quotations, page references, claims, findings, or gaps.
- Preserve paper identifiers, source URLs, documents, page locations, quotations, and text offsets.
- Keep explicitly reported findings, limitations, evidence-supported interpretations, cross-paper inferences, and unsupported assumptions distinct.
- Exact quote matching and semantic support are separate checks. Unsupported claims must not enter gap reasoning.
- A small corpus cannot establish global research novelty.

## Development process

- Implement one approved module at a time, with schemas, tests, and documentation.
- Keep generated papers, extracted text, reports, databases, and private configuration out of Git.
- Record failed and retriable processing states; do not silently drop records.
- Run relevant tests and report actual output before declaring a feature complete.
- Seek approval for material architecture, dependency, model, or workflow changes.

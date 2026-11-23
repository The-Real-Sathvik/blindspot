# Project status

## Current phase

Phase 1 — foundational provenance model.

## Implemented

- Repository guidance and Git ignore rules.
- Pydantic schemas for research jobs, papers, documents, evidence, claims, claim evidence, and verification results.
- SQLAlchemy 2.x models with foreign keys, uniqueness checks, and relationship mappings.
- SQLite foreign-key enforcement on every connection.
- Initial Alembic migration.
- Focused schema and persistence tests.

## Not implemented

- Live academic retrieval and OpenAlex integration.
- Deduplication, relevance screening, downloading, or PDF extraction.
- Ollama, claim extraction, semantic verification, or gap detection.
- ChromaDB, LangGraph, FastAPI endpoints, React UI, and background services.

## Verification status

Verified on 2026-11-23 with native Windows Python 3.12.4 at `C:\Users\Sathvik Karumoju\AppData\Local\Programs\Python\Python312\python.exe`. The project virtual environment is `D:\Blindspot\.venv`.

- Installed only the declared Phase 1 packages: SQLAlchemy 2.0.58, Alembic 1.20.1, Pydantic 2.14.4, and pytest 8.4.2 (plus their transitive dependencies).
- Pytest: `5 passed` in 0.16 seconds on the final run.
- Alembic initial migration `20261123_0001` applied successfully to `backend/blindspot.db`.
- Alembic reports `20261123_0001 (head)` from the repository root after path verification.
- Schema inspection confirmed all expected tables, declared foreign keys, and uniqueness rules for DOI, OpenAlex ID, and the research-job/paper pair.
- The application database engine enables SQLite foreign-key enforcement and rejected a deliberately invalid foreign-key insert.

No model, paper, server, or background service was started.

## Resolved issue

The Python installation is available by its direct user-local executable but is not registered with the `py` launcher or the `python` command in this terminal. Project commands use `.venv\Scripts\python.exe` after environment creation.

Alembic configuration now resolves both its import path and SQLite database path relative to `backend/alembic.ini`, so root-level commands such as `python -m alembic -c backend\alembic.ini current` target `backend/blindspot.db` correctly.

An unintended root-level `blindspot.db` briefly created while validating the earlier relative URL was removed. The verified database is `backend/blindspot.db`.

## Next approved task

After Python setup and successful Phase 1 test execution, implement the OpenAlex retrieval adapter and local, testable metadata normalisation only after explicit approval.

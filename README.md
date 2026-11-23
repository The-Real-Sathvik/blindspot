# Blindspot

Blindspot is a local-first, evidence-grounded research assistant for discovering and validating potential research gaps. It is designed to make every important conclusion traceable to the research literature that supports it.

## Current phase

Phase 1 foundations are implemented in source form: the repository documentation, Pydantic data contracts, SQLAlchemy persistence model, Alembic initial migration, and focused tests. No retrieval, document downloading, PDF extraction, AI model integration, frontend, background worker, or research-paper download is included.

## Planned stack

- Python, FastAPI, Pydantic
- SQLAlchemy 2.x, Alembic, SQLite
- React and Vite (later phase)
- Ollama with Llama 3.1 8B Instruct Q4_K_M (later phase)

## Local setup

This project requires a native Windows Python 3.11+ installation. Once Python is available:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
pytest backend\tests -q
alembic -c backend\alembic.ini upgrade head
```

The migration creates a local SQLite database at `backend/blindspot.db` by default. Generated data belongs under `data/` and is ignored by Git.

See [the architecture](docs/ARCHITECTURE.md), [project status](docs/PROJECT_STATUS.md), and [evaluation plan](docs/EVALUATION_PLAN.md).

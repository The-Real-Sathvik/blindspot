"""Run one development chunk. Mock mode rolls back; live execution is explicit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.db.session import create_session_factory, create_sqlite_engine
from app.services.claim_extraction_v3 import PROMPT_VERSION, ClaimExtractionResponseV3, run_development_extraction
from app.services.cloud_inference import CloudInferenceRouter
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider
from app.services.ollama_client import InferenceMetrics
from app.services.web_claims import ensure_processed_source


class MockAbstentionRunner:
    def generate(self, role, prompt, schema):
        return ClaimExtractionResponseV3(claims=[]), InferenceMetrics(0.0, 0, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--chunk-id", required=True)
    parser.add_argument("--provider", choices=("gemini", "groq"), default="gemini")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="Mock abstention; roll back all database writes")
    mode.add_argument("--execute", action="store_true", help="Explicitly allow one cloud request")
    args = parser.parse_args()
    path = BACKEND / "tests" / "fixtures" / "claim_extraction_reference_annotations_phase_4b_3a_v2.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    held_out_docs = {entry["source_document_id"] for entry in entries if entry["dataset_split"] == "held_out"}
    development = frozenset(entry["evidence_chunk_id"] for entry in entries if entry["dataset_split"] == "development" and entry["source_document_id"] not in held_out_docs)
    provider = InferenceProvider(args.provider)
    model = "gemini-3.5-flash-lite" if provider is InferenceProvider.GEMINI else "openai/gpt-oss-120b"
    assignment = AgentModelAssignment(provider=provider, model_tag=model, prompt_template_version=PROMPT_VERSION)
    runner = MockAbstentionRunner() if args.mock else CloudInferenceRouter.from_environment(BlindspotModelConfig(claim_extraction=assignment))
    engine = create_sqlite_engine(f"sqlite:///{(BACKEND / 'blindspot.db').as_posix()}")
    try:
        with create_session_factory(engine)() as session:
            result = run_development_extraction(session, job_id=args.job_id, chunk_id=args.chunk_id, development_chunk_ids=development, assignment=assignment, runner=runner, source_guard=ensure_processed_source)
            if args.mock: session.rollback()
            else: session.commit()
            print(json.dumps({"mode": "mock_rolled_back" if args.mock else "cloud", **result.__dict__}))
            return 1 if result.outcome in ("failed_inference", "partial_error") else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

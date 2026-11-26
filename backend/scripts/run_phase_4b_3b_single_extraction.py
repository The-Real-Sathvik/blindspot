"""One guarded Phase 4B.3B development-only live extraction; no retries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from threading import Event, Thread

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.chdir(BACKEND_ROOT)

from sqlalchemy import select

from app.db.session import create_session_factory, create_sqlite_engine
from app.models.research import ClaimEvidence, ExtractedClaim, EvidenceChunk, ModelRun, ResearchJob
from app.schemas.research import AgentRole
from app.services.claim_extraction import ClaimExtractionService, PROMPT_TEMPLATE_VERSION
from app.services.claim_extraction_evaluation import check_memory_safety
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, SequentialModelRunner
from app.services.ollama_benchmark import available_memory_mb
from app.services.ollama_client import MODEL_TAG, OllamaClient


PAPER_ID = "b4e2045d-40e8-4e90-904c-acb33bee80ab"
CHUNK_ID = "8e85e888-fbe8-423b-b979-360bb174e9c1"
JOB_TOPIC = "Phase 4B.3B controlled development extraction: covid-dataset-size"


def main() -> None:
    safety = check_memory_safety()
    if not safety.safe_to_infer:
        print(json.dumps({"inference_ran": False, "safety": safety.__dict__}))
        return
    engine = create_sqlite_engine("sqlite:///blindspot.db")
    session = create_session_factory(engine)()
    client = OllamaClient(model=MODEL_TAG, context_tokens=4096, output_tokens=256)
    stop = Event(); minimum = safety.available_memory_mb

    def sample_memory() -> None:
        nonlocal minimum
        while not stop.wait(0.25):
            minimum = min(minimum, available_memory_mb())

    sampler = Thread(target=sample_memory, daemon=True)
    try:
        chunk = session.get(EvidenceChunk, CHUNK_ID)
        if chunk is None:
            raise RuntimeError("selected development evidence chunk is missing")
        job = session.scalar(select(ResearchJob).where(ResearchJob.topic == JOB_TOPIC))
        if job is None:
            job = ResearchJob(topic=JOB_TOPIC)
            session.add(job); session.flush()
        assignment = AgentModelAssignment(model_tag=MODEL_TAG, context_tokens=4096, output_tokens=256,
            prompt_template_version=PROMPT_TEMPLATE_VERSION)
        runner = SequentialModelRunner(client, BlindspotModelConfig(claim_extraction=assignment))
        sampler.start()
        result = ClaimExtractionService(runner).process_chunk(session, research_job=job, paper_id=PAPER_ID, chunk=chunk, assignment=assignment)
        stop.set(); sampler.join(timeout=1)
        client.unload_model(MODEL_TAG)
        after = available_memory_mb()
        run = session.get(ModelRun, result.model_run_id)
        if run is not None:
            run.available_memory_before_mb = safety.available_memory_mb
            run.available_memory_minimum_mb = minimum
            run.available_memory_after_mb = after
        claims = session.scalars(select(ExtractedClaim).where(ExtractedClaim.model_run_id == result.model_run_id)).all()
        output = {
            "inference_ran": True,
            "model_run_id": result.model_run_id,
            "outcome": result.outcome.value,
            "accepted_count": result.accepted_count,
            "rejected_count": result.rejected_count,
            "reused": result.reused,
            "memory": {"before_mb": safety.available_memory_mb, "minimum_mb": minimum, "after_mb": after},
            "claims": [{
                "claim_text": claim.claim_text, "claim_category": claim.claim_type.value,
                "uncertainty": claim.uncertainty_or_missing_information,
                "evidence": [{"quotation": evidence.quotation_text, "chunk_id": evidence.evidence_chunk_id,
                              "chunk_offsets": [evidence.quote_start_offset, evidence.quote_end_offset],
                              "quotation_status": evidence.quotation_status.value,
                              "source_location_status": evidence.source_location_status.value}
                             for evidence in claim.evidence_links],
            } for claim in claims],
            "model_run": {"status": run.status.value if run else None, "output_tokens": run.output_tokens if run else None,
                          "tokens_per_second": run.tokens_per_second if run else None, "runtime_seconds": run.runtime_seconds if run else None,
                          "error_category": run.error_category if run else None, "error_summary": run.error_summary if run else None},
        }
        session.commit()
        print(json.dumps(output, ensure_ascii=False))
    finally:
        stop.set(); sampler.join(timeout=1)
        client.unload_model(MODEL_TAG)
        session.close(); engine.dispose()


if __name__ == "__main__":
    main()

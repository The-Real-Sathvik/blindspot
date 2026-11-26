"""One explicitly invoked local V3 acceptance attempt; no retry or fallback."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from threading import Event, Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.session import create_session_factory, create_sqlite_engine
from app.models.research import EvidenceChunk, ModelRun, ResearchJobPaper, SourceDocument
from app.schemas.research import AgentRole
from app.services.claim_extraction_evaluation import check_memory_safety
from app.services.claim_extraction_v3 import NoAdmissiblePassage, PROMPT_VERSION, run_development_extraction, select_evidence_spans
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider, SequentialModelRunner
from app.services.ollama_benchmark import available_memory_mb
from app.services.ollama_client import MODEL_TAG, OllamaClient
from app.services.web_claims import ensure_processed_source


def _development_chunks() -> frozenset[str]:
    rows = json.loads((ROOT / "tests" / "fixtures" / "claim_extraction_reference_annotations_phase_4b_3a_v2.json").read_text(encoding="utf-8"))
    held = {row["source_document_id"] for row in rows if row["dataset_split"] == "held_out"}
    return frozenset(row["evidence_chunk_id"] for row in rows if row["dataset_split"] == "development" and row["source_document_id"] not in held)


def main() -> int:
    safety = check_memory_safety()
    if not safety.safe_to_infer:
        print(json.dumps({"inference_ran": False, "ram_before_mb": safety.available_memory_mb, "ram_gate_mb": safety.required_memory_mb, "reason": safety.reason}))
        return 0
    engine = create_sqlite_engine(f"sqlite:///{(ROOT / 'blindspot.db').as_posix()}")
    session = create_session_factory(engine)()
    client = OllamaClient(model=MODEL_TAG, context_tokens=4096, output_tokens=256)
    stop = Event(); sampler = None; minimum = safety.available_memory_mb
    try:
        assignment = AgentModelAssignment(provider=InferenceProvider.OLLAMA, model_tag=MODEL_TAG, context_tokens=4096, output_tokens=256, prompt_template_version=PROMPT_VERSION)
        development = _development_chunks()
        selected = None
        for chunk in session.scalars(select(EvidenceChunk).where(EvidenceChunk.id.in_(development)).order_by(EvidenceChunk.id)).all():
            document = session.get(SourceDocument, chunk.source_document_id)
            job_id = session.scalar(select(ResearchJobPaper.research_job_id).where(ResearchJobPaper.paper_id == document.paper_id).order_by(ResearchJobPaper.research_job_id).limit(1)) if document else None
            if not document or not job_id:
                continue
            try:
                page = ensure_processed_source(chunk, document)
                spans = select_evidence_spans(chunk, page, assignment)
            except Exception:
                continue
            selected = (job_id, chunk, document, spans)
            break
        if selected is None:
            print(json.dumps({"inference_ran": False, "ram_before_mb": safety.available_memory_mb, "reason": "No verified non-held-out chunk has an admissible complete V3 span."}))
            return 0
        job_id, chunk, document, spans = selected
        session.rollback()  # V3 owns its immediate SQLite transaction.
        def sample() -> None:
            nonlocal minimum
            while not stop.wait(.25): minimum = min(minimum, available_memory_mb())
        sampler = Thread(target=sample, daemon=True); sampler.start()
        runner = SequentialModelRunner(client, BlindspotModelConfig(claim_extraction=assignment))
        result = run_development_extraction(session, job_id=job_id, chunk_id=chunk.id, development_chunk_ids=development,
            assignment=assignment, runner=runner, source_guard=ensure_processed_source, select_complete_spans=True)
        stop.set(); sampler.join(timeout=1)
        run = session.get(ModelRun, result.model_run_id)
        after = available_memory_mb()
        if run:
            run.available_memory_before_mb, run.available_memory_minimum_mb, run.available_memory_after_mb = safety.available_memory_mb, minimum, after
        claims = [] if run is None else [c for c in run.extracted_claims]
        session.commit()
        print(json.dumps({"inference_ran": True, "ram_before_mb": safety.available_memory_mb, "ram_minimum_mb": minimum, "ram_after_mb": after,
            "model_run_id": result.model_run_id, "outcome": result.outcome, "accepted_count": result.accepted_count, "rejected_count": result.rejected_count,
            "rejection_codes": result.rejection_codes, "chunk_id": chunk.id, "page": chunk.page_number, "span_ids": [s.evidence_id for s in spans],
            "model": MODEL_TAG, "output_tokens": run.output_tokens if run else None, "tokens_per_second": run.tokens_per_second if run else None,
            "runtime_seconds": run.runtime_seconds if run else None, "error_category": run.error_category if run else None,
            "claims": [{"statement": c.claim_text, "category": c.claim_type.value, "evidence_count": len(c.evidence_links)} for c in claims]}))
        return 0
    finally:
        stop.set()
        if sampler: sampler.join(timeout=1)
        client.unload_model(MODEL_TAG)
        session.close(); engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

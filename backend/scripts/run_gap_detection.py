"""Run a synthetic mock demo, or explicitly process a job and allowed chunks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from sqlalchemy import select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.models.research import CandidateGap, ClaimEvidence, EvidenceChunk, ExtractedClaim, Paper, ResearchJob, ResearchJobPaper, SourceDocument, VerificationResult
from app.schemas.research import CitationCheckStatus, ClaimType, SemanticSupportStatus
from app.services.cloud_inference import CloudInferenceRouter
from app.services.gap_detection import PROMPT_VERSION, prepare_packet, run_gap_detection
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider
from app.services.ollama_client import InferenceMetrics


class MockRunner:
    def __init__(self, packet): self.packet = packet
    def generate(self, role, prompt, schema):
        supported = [item for item in self.packet if item["verification"] == "supported"]
        refs = [{"paper_id": item["paper_id"], "claim_id": item["claim_id"], "evidence_id": item["evidence_id"], "role": "supporting"} for item in supported[:2]]
        candidate = {"statement": "A direct comparison may be missing from the supplied records.", "category": "underexplored_comparison", "references": refs,
                     "explanation": "The two synthetic records describe different methods.", "counterevidence": "Other records were not searched.",
                     "uncertainty": "This synthetic example is not research evidence.", "validation_status": "not_yet_validated"}
        return schema(gaps=[candidate]), InferenceMetrics(0.0, 0, None)


def demo(session):
    job = ResearchJob(topic="Synthetic gap detection demonstration")
    chunks = []
    for i in range(2):
        paper = Paper(title=f"Synthetic paper {i + 1}", authors=[], open_access_metadata={})
        doc = SourceDocument(paper=paper, document_type="synthetic")
        passage = f"Synthetic method {i + 1} was tested."
        chunk = EvidenceChunk(source_document=doc, page_number=1, chunk_index=0, start_offset=0, end_offset=len(passage), text=passage)
        claim = ExtractedClaim(paper=paper, claim_type=ClaimType.METHODOLOGY, claim_text=passage)
        evidence = ClaimEvidence(claim=claim, evidence_chunk=chunk, quotation_text=passage, quote_start_offset=0, quote_end_offset=len(passage), quotation_status=CitationCheckStatus.VALID, source_location_status=CitationCheckStatus.VALID)
        review = VerificationResult(claim=claim, semantic_status=SemanticSupportStatus.SUPPORTED, rationale="Synthetic fixture only.")
        session.add_all([ResearchJobPaper(research_job=job, paper=paper), evidence, review]); chunks.append(chunk)
    session.commit()
    job_id = job.id; ids = frozenset(chunk.id for chunk in chunks)
    packet = prepare_packet(session, job_id, allowed_chunk_ids=ids)
    session.commit()
    return job_id, ids, packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Use an in-memory synthetic database; no cloud calls")
    parser.add_argument("--job-id")
    parser.add_argument("--chunk-id", action="append", default=[], help="Explicitly approved non-held-out chunk; repeat as needed")
    parser.add_argument("--provider", choices=["groq", "gemini"], default="groq")
    parser.add_argument("--execute", action="store_true", help="Explicitly enable a cloud request for an existing job")
    args = parser.parse_args()
    if args.demo and (args.execute or args.job_id or args.chunk_id): parser.error("--demo cannot be combined with job or cloud options")
    if not args.demo and (not args.execute or not args.job_id or not args.chunk_id): parser.error("Provide --demo, or --execute with --job-id and approved --chunk-id values")
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:" if args.demo else f"sqlite:///{(BACKEND / 'blindspot.db').as_posix()}")
    if args.demo: Base.metadata.create_all(engine)
    provider = InferenceProvider(args.provider)
    assignment = AgentModelAssignment(provider=provider, model_tag="openai/gpt-oss-120b" if provider is InferenceProvider.GROQ else "gemini-3.5-flash-lite", prompt_template_version=PROMPT_VERSION)
    try:
        with create_session_factory(engine)() as session:
            if args.demo:
                job_id, allowed, packet = demo(session); runner = MockRunner(packet)
            else:
                job_id, allowed = args.job_id, frozenset(args.chunk_id)
                runner = CloudInferenceRouter.from_environment(BlindspotModelConfig(gap_detection=assignment))
            result = run_gap_detection(session, job_id=job_id, allowed_chunk_ids=allowed, assignment=assignment, runner=runner)
            examples = []
            if args.demo and result.model_run_id:
                for gap in session.scalars(select(CandidateGap).where(CandidateGap.model_run_id == result.model_run_id)):
                    examples.append({"statement": gap.statement, "category": gap.category, "validation_status": gap.validation_status, "paper_count": len({ref.paper_id for ref in gap.references})})
            if args.demo: session.rollback()
            else: session.commit()
            print(json.dumps({"mode": "synthetic_demo" if args.demo else "cloud", **result.__dict__, "synthetic_examples": examples}))
            return 1 if result.outcome in {"failed_inference", "partial_error", "insufficient_evidence"} else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

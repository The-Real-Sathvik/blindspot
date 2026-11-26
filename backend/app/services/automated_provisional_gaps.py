"""Offline eligibility and persistence guards for automated provisional gaps."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.research import (AutomatedProvisionalGap, AutomatedProvisionalGapReference, AutomatedSemanticAssessment,
    ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJob, ResearchJobPaper, SourceDocument)
from app.schemas.research import CitationCheckStatus, SemanticSupportStatus
from app.services.automated_semantic_assessment import eligibility
from app.services.model_config import AgentModelAssignment
from app.services.model_runs import ModelRunRecorder
from app.schemas.research import AgentRole
from app.services.cloud_inference import CloudInferenceError

VERSION = "automated-provisional-gap-v1"


class GapReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str
    claim_id: str
    assessment_id: str
    evidence_id: str
    role: str = Field(pattern="^(supporting|counterevidence)$")


class ProvisionalGapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=1600)
    rationale: str = Field(min_length=1, max_length=2400)
    uncertainty: str = Field(min_length=1, max_length=1600)
    references: list[GapReferenceResponse] = Field(min_length=2, max_length=12)


@dataclass(frozen=True)
class AutomatedPacket:
    supporting: tuple[dict, ...]
    counterevidence: tuple[dict, ...]
    @property
    def eligible(self): return len({item['paper_id'] for item in self.supporting}) >= 2


def prepare_packet(session: Session, job_id: str, *, allowed_chunk_ids: frozenset[str]) -> AutomatedPacket:
    rows = session.execute(select(ExtractedClaim, ClaimEvidence, EvidenceChunk, SourceDocument, Paper, AutomatedSemanticAssessment, ModelRun)
        .join(ModelRun, ExtractedClaim.model_run_id == ModelRun.id).join(ClaimEvidence, ClaimEvidence.claim_id == ExtractedClaim.id)
        .join(EvidenceChunk, ClaimEvidence.evidence_chunk_id == EvidenceChunk.id).join(SourceDocument, EvidenceChunk.source_document_id == SourceDocument.id)
        .join(Paper, ExtractedClaim.paper_id == Paper.id).join(ResearchJobPaper, ResearchJobPaper.paper_id == Paper.id)
        .join(AutomatedSemanticAssessment, AutomatedSemanticAssessment.claim_id == ExtractedClaim.id)
        .where(ResearchJobPaper.research_job_id == job_id, ModelRun.research_job_id == job_id).order_by(AutomatedSemanticAssessment.assessed_at.desc())).all()
    support, counter, seen = [], [], set()
    for claim, evidence, chunk, document, paper, assessment, claim_run in rows:
        if claim.id in seen or chunk.id not in allowed_chunk_ids or document.paper_id != paper.id or claim.paper_id != paper.id:
            continue
        assessment_run = session.get(ModelRun, assessment.model_run_id) if assessment.model_run_id else None
        if assessment_run is None or assessment_run.research_job_id != job_id:
            continue
        if evidence.quotation_status != CitationCheckStatus.VALID or evidence.source_location_status != CitationCheckStatus.VALID:
            continue
        try: current = eligibility(claim)
        except Exception: continue
        if current != assessment.evidence_snapshot: continue
        item={"paper_id":paper.id,"claim_id":claim.id,"assessment_id":assessment.id,"evidence_id":evidence.id,
              "chunk_id":chunk.id,"page":chunk.page_number,"claim":claim.claim_text,"quotation":evidence.quotation_text,
              "status":assessment.assessment_status.value,"rationale":assessment.rationale,"uncertainty":assessment.uncertainty}
        seen.add(claim.id)
        if assessment.assessment_status is SemanticSupportStatus.SUPPORTED: support.append(item)
        elif assessment.assessment_status in {SemanticSupportStatus.PARTIALLY_SUPPORTED, SemanticSupportStatus.CONTRADICTED}: counter.append(item)
    return AutomatedPacket(tuple(support), tuple(counter))


def persist(session: Session, *, job_id: str, model_run_id: str, statement: str, rationale: str, uncertainty: str, references: list[dict]) -> AutomatedProvisionalGap:
    if not statement.strip() or len({r['paper_id'] for r in references if r.get('role') == 'supporting'}) < 2:
        raise ValueError('A provisional gap requires supporting assessments from two papers.')
    # The model sees stable ClaimEvidence IDs, never mutable chunk locations.
    # Resolve the job-owned chunks independently before validating each ID.
    ids=frozenset(session.scalars(select(EvidenceChunk.id).join(SourceDocument)
        .join(ResearchJobPaper, ResearchJobPaper.paper_id == SourceDocument.paper_id)
        .where(ResearchJobPaper.research_job_id == job_id)).all())
    packet=prepare_packet(session,job_id,allowed_chunk_ids=ids)
    valid={(x['paper_id'],x['claim_id'],x['assessment_id'],x['evidence_id']):x for x in packet.supporting+packet.counterevidence}
    supporting={(x['paper_id'],x['claim_id'],x['assessment_id'],x['evidence_id']) for x in packet.supporting}
    if any((r['paper_id'],r['claim_id'],r['assessment_id'],r['evidence_id']) not in valid for r in references):
        raise ValueError('Gap references are not current automated evidence.')
    if any(r.get('role') == 'supporting' and (r['paper_id'],r['claim_id'],r['assessment_id'],r['evidence_id']) not in supporting for r in references):
        raise ValueError('Only supported automated assessments may support a provisional gap.')
    identity=sha256(json.dumps([job_id,statement.strip(),sorted((r['assessment_id'],r['role']) for r in references)],separators=(',',':')).encode()).hexdigest()
    row=AutomatedProvisionalGap(research_job_id=job_id,model_run_id=model_run_id,identity_hash=identity,statement=statement.strip(),rationale=rationale.strip(),uncertainty=uncertainty.strip())
    session.add(row); session.flush()
    for ref in references:
        item=valid[(ref['paper_id'],ref['claim_id'],ref['assessment_id'],ref['evidence_id'])]
        session.add(AutomatedProvisionalGapReference(gap=row,paper_id=item['paper_id'],claim_id=item['claim_id'],automated_assessment_id=item['assessment_id'],claim_evidence_id=item['evidence_id'],role=ref['role']))
    return row


def prompt_for(packet: AutomatedPacket) -> str:
    """A bounded, source-id-only packet; quotations remain in persisted evidence."""
    return ("Compare only these source-grounded, automated-supported findings from distinct papers. "
            "Return one provisional research gap, never a global novelty claim. References must use only supplied IDs. "
            "Return JSON with statement, rationale, uncertainty, references.\n" +
            json.dumps({"supporting": packet.supporting, "counterevidence": packet.counterevidence}, ensure_ascii=True, separators=(",", ":")))


def run(session: Session, *, job_id: str, allowed_chunk_ids: frozenset[str], assignment: AgentModelAssignment, runner):
    """Run the automated-only Agent 2 path. It never reads human reviews."""
    packet = prepare_packet(session, job_id, allowed_chunk_ids=allowed_chunk_ids)
    if not packet.eligible:
        return None, "insufficient_evidence"
    # Finish all packet reads before any provider request. Persistence revalidates
    # the packet below, so a concurrent source change cannot become a gap.
    session.commit()
    job = session.get(ResearchJob, job_id)
    if job is None:
        raise ValueError("Unknown research job.")
    assignment = AgentModelAssignment.model_validate({**assignment.model_dump(), "prompt_template_version": VERSION})
    recorder = ModelRunRecorder()
    record = recorder.start(session, research_job=job, role=AgentRole.GAP_DETECTION, assignment=assignment,
                            input_reference=f"{job_id}:{VERSION}:{len(packet.supporting)}")
    session.commit()
    try:
        response, metrics = runner.generate(AgentRole.GAP_DETECTION, prompt_for(packet), ProvisionalGapResponse)
        response = ProvisionalGapResponse.model_validate(response)
        references = [item.model_dump() for item in response.references]
        gap = persist(session, job_id=job_id, model_run_id=record.id, statement=response.statement,
                      rationale=response.rationale, uncertainty=response.uncertainty, references=references)
    except Exception as error:
        record = session.get(ModelRun, record.id)
        recorder.fail(record, error if isinstance(error, CloudInferenceError) else RuntimeError("Provisional gap response validation failed."))
        session.commit()
        return None, "failed"
    recorder.succeed(record, output=response.model_dump_json(), metrics=metrics)
    session.commit()
    return gap, "generated"

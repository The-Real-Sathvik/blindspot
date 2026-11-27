"""Offline eligibility and persistence guards for automated provisional gaps."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.research import (AutomatedProvisionalGap, AutomatedProvisionalGapReference, AutomatedSemanticAssessment,
    ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJob, ResearchJobPaper, SourceDocument)
from app.schemas.research import CitationCheckStatus, SemanticSupportStatus
from app.services.automated_semantic_assessment import eligibility
from app.services.model_config import AgentModelAssignment
from app.services.model_runs import ModelRunRecorder
from app.schemas.research import AgentRole
from app.services.cloud_inference import CloudInferenceError, CloudResponseError
from app.services.model_runs import safe_hash

VERSION = "automated-provisional-gap-v2"
_REFERENCE_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_GAP_RESPONSE = re.compile(
    rf"\AGAP: (?P<statement>[^\r\n]{{1,280}})\r?\n"
    rf"SUPPORTING: (?P<supporting>{_REFERENCE_ID},{_REFERENCE_ID})\r?\n"
    rf"COUNTEREVIDENCE: (?P<counterevidence>NONE|{_REFERENCE_ID}(?:,{_REFERENCE_ID})?)\r?\n"
    rf"RATIONALE: (?P<rationale>[^\r\n]{{1,400}})\r?\n"
    rf"UNCERTAINTY: (?P<uncertainty>[^\r\n]{{1,280}})\Z"
)


class ProvisionalGapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abstained: bool = False
    statement: str = Field(default="", max_length=280)
    rationale: str = Field(default="", max_length=400)
    uncertainty: str = Field(default="", max_length=280)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=2)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_response(self):
        if self.abstained:
            if self.statement or self.rationale or self.uncertainty or self.supporting_evidence_ids or self.counterevidence_ids:
                raise ValueError("An abstention cannot include a gap or references.")
        elif not self.statement.strip() or not self.rationale.strip() or not self.uncertainty.strip() or len(self.supporting_evidence_ids) != 2:
            raise ValueError("A gap response requires concise text and exactly two supporting evidence IDs.")
        if len(set(self.supporting_evidence_ids)) != len(self.supporting_evidence_ids) or len(set(self.counterevidence_ids)) != len(self.counterevidence_ids):
            raise ValueError("Gap evidence references must be unique.")
        return self


def parse_gemini_gap_response(content, schema: type[BaseModel], diagnostics: dict) -> BaseModel:
    if schema is not ProvisionalGapResponse:
        raise CloudResponseError("unsupported_plain_text_schema", diagnostics)
    if not isinstance(content, str):
        raise CloudResponseError("missing_response_content", diagnostics)
    if content == "ABSTAIN":
        diagnostics["plain_text_contract"] = "valid"
        return ProvisionalGapResponse(abstained=True)
    match = _GAP_RESPONSE.fullmatch(content)
    if match is None:
        diagnostics["plain_text_contract"] = "invalid"
        raise CloudResponseError("invalid_plain_text_contract", diagnostics)
    counterevidence = match.group("counterevidence")
    try:
        response = ProvisionalGapResponse(
            statement=match.group("statement"),
            rationale=match.group("rationale"),
            uncertainty=match.group("uncertainty"),
            supporting_evidence_ids=match.group("supporting").split(","),
            counterevidence_ids=[] if counterevidence == "NONE" else counterevidence.split(","),
        )
    except ValueError:
        diagnostics["plain_text_contract"] = "invalid"
        raise CloudResponseError("invalid_plain_text_contract", diagnostics) from None
    diagnostics["plain_text_contract"] = "valid"
    return response


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
        item={"paper_id":paper.id,"paper_title":paper.title,"year":paper.publication_year,
              "claim_id":claim.id,"assessment_id":assessment.id,"evidence_id":evidence.id,
              "chunk_id":chunk.id,"page":chunk.page_number,"claim":claim.claim_text,"quotation":evidence.quotation_text,
              "status":assessment.assessment_status.value,"rationale":assessment.rationale,"uncertainty":assessment.uncertainty}
        seen.add(claim.id)
        if assessment.assessment_status is SemanticSupportStatus.SUPPORTED: support.append(item)
        elif assessment.assessment_status in {SemanticSupportStatus.PARTIALLY_SUPPORTED, SemanticSupportStatus.CONTRADICTED}: counter.append(item)
    return AutomatedPacket(tuple(support), tuple(counter))


def persist(session: Session, *, job_id: str, model_run_id: str, allowed_chunk_ids: frozenset[str], statement: str, rationale: str, uncertainty: str, references: list[dict]) -> AutomatedProvisionalGap:
    if (not statement.strip() or len(statement) > 280 or not rationale.strip() or len(rationale) > 400
            or not uncertainty.strip() or len(uncertainty) > 280
            or len({r['paper_id'] for r in references if r.get('role') == 'supporting'}) < 2):
        raise ValueError('A provisional gap requires supporting assessments from two papers.')
    # Revalidate the exact allowlist supplied by the autonomous runner. It must
    # be a subset of this job's finalized, source-verified analysis papers.
    selected_ids=frozenset(session.scalars(select(EvidenceChunk.id).join(SourceDocument)
        .join(ResearchJobPaper, ResearchJobPaper.paper_id == SourceDocument.paper_id)
        .where(ResearchJobPaper.research_job_id == job_id, ResearchJobPaper.analysis_selected.is_(True))).all())
    if not allowed_chunk_ids.issubset(selected_ids):
        raise ValueError('Gap evidence is outside the finalized analysis paper allowlist.')
    packet=prepare_packet(session,job_id,allowed_chunk_ids=allowed_chunk_ids)
    valid={(x['paper_id'],x['claim_id'],x['assessment_id'],x['evidence_id']):x for x in packet.supporting+packet.counterevidence}
    supporting={(x['paper_id'],x['claim_id'],x['assessment_id'],x['evidence_id']) for x in packet.supporting}
    counterevidence={(x['paper_id'],x['claim_id'],x['assessment_id'],x['evidence_id']) for x in packet.counterevidence}
    if any((r['paper_id'],r['claim_id'],r['assessment_id'],r['evidence_id']) not in valid for r in references):
        raise ValueError('Gap references are not current automated evidence.')
    if any(r.get('role') == 'supporting' and (r['paper_id'],r['claim_id'],r['assessment_id'],r['evidence_id']) not in supporting for r in references):
        raise ValueError('Only supported automated assessments may support a provisional gap.')
    if any(r.get('role') == 'counterevidence' and (r['paper_id'],r['claim_id'],r['assessment_id'],r['evidence_id']) not in counterevidence for r in references):
        raise ValueError('Counterevidence references must come from partial or contradicted assessments.')
    if any(r.get('role') not in {'supporting', 'counterevidence'} for r in references):
        raise ValueError('Gap references must have a supported evidence role.')
    identity=sha256(json.dumps([job_id,statement.strip(),sorted((r['assessment_id'],r['role']) for r in references)],separators=(',',':')).encode()).hexdigest()
    row=AutomatedProvisionalGap(research_job_id=job_id,model_run_id=model_run_id,identity_hash=identity,statement=statement.strip(),rationale=rationale.strip(),uncertainty=uncertainty.strip())
    session.add(row); session.flush()
    for ref in references:
        item=valid[(ref['paper_id'],ref['claim_id'],ref['assessment_id'],ref['evidence_id'])]
        session.add(AutomatedProvisionalGapReference(gap=row,paper_id=item['paper_id'],claim_id=item['claim_id'],automated_assessment_id=item['assessment_id'],claim_evidence_id=item['evidence_id'],role=ref['role']))
    return row


def prompt_for(packet: AutomatedPacket) -> str:
    """Request one concise gap using exact database evidence IDs only."""
    data = {
        "supporting": [{"paper_id": item["paper_id"], "paper_title": item["paper_title"], "year": item["year"],
                        "evidence_id": item["evidence_id"],
                        "claim": item["claim"], "quotation": item["quotation"]} for item in packet.supporting],
        "counterevidence": [{"paper_id": item["paper_id"], "paper_title": item["paper_title"], "year": item["year"],
                             "evidence_id": item["evidence_id"],
                             "claim": item["claim"], "quotation": item["quotation"]} for item in packet.counterevidence],
    }
    return ("Compare only the supplied source-grounded findings. Return at most one possible research gap; "
            "never claim global novelty. Do not invent citations, paper names, quotations or pages. "
            "Use exactly two SUPPORTING evidence_id values from distinct papers and only supplied IDs. "
            "Use up to two COUNTEREVIDENCE IDs, or NONE. If no defensible gap exists, return exactly ABSTAIN. "
            "Otherwise return exactly five lines:\n"
            "GAP: <one concise possible research gap, at most 280 characters>\n"
            "SUPPORTING: <evidence_id>,<evidence_id>\n"
            "COUNTEREVIDENCE: <NONE or one or two evidence IDs>\n"
            "RATIONALE: <one concise sentence>\n"
            "UNCERTAINTY: <one concise sentence>\n"
            + json.dumps(data, ensure_ascii=True, separators=(",", ":")))


def _resolve_references(response: ProvisionalGapResponse, packet: AutomatedPacket) -> list[dict]:
    eligible_support = {item["evidence_id"]: item for item in packet.supporting}
    eligible_counter = {item["evidence_id"]: item for item in packet.counterevidence}
    supporting = [eligible_support.get(evidence_id) for evidence_id in response.supporting_evidence_ids]
    if any(item is None for item in supporting) or len({item["paper_id"] for item in supporting if item}) < 2:
        raise ValueError("Gap support must cite eligible evidence from two distinct papers.")
    counterevidence = [eligible_counter.get(evidence_id) for evidence_id in response.counterevidence_ids]
    if any(item is None for item in counterevidence):
        raise ValueError("Gap counterevidence must cite eligible counterevidence.")
    return [
        {"paper_id": item["paper_id"], "claim_id": item["claim_id"],
         "assessment_id": item["assessment_id"], "evidence_id": item["evidence_id"], "role": role}
        for role, items in (("supporting", supporting), ("counterevidence", counterevidence))
        for item in items
    ]


def run(session: Session, *, job_id: str, allowed_chunk_ids: frozenset[str], assignment: AgentModelAssignment, runner):
    """Run the automated-only Agent 2 path. It never reads human reviews."""
    packet = prepare_packet(session, job_id, allowed_chunk_ids=allowed_chunk_ids)
    if not packet.eligible:
        return None, "insufficient_evidence"
    checkpoint = json.dumps({
        "job_id": job_id,
        "version": VERSION,
        "supporting": sorted((item["paper_id"], item["claim_id"], item["assessment_id"], item["evidence_id"]) for item in packet.supporting),
        "counterevidence": sorted((item["paper_id"], item["claim_id"], item["assessment_id"], item["evidence_id"]) for item in packet.counterevidence),
    }, separators=(",", ":"))
    checkpoint_hash = safe_hash(checkpoint)
    previous_run = session.scalar(select(ModelRun).where(
        ModelRun.research_job_id == job_id,
        ModelRun.agent_role == AgentRole.GAP_DETECTION,
        ModelRun.prompt_template_version == VERSION,
        ModelRun.input_reference_hash == checkpoint_hash,
    ).order_by(ModelRun.created_at.desc()).limit(1))
    if previous_run is not None:
        if previous_run.status.value == "failed":
            return None, "already_attempted"
        if previous_run.model_config.get("agent_outcome") == "abstained":
            return None, "abstained"
        saved = session.scalar(select(AutomatedProvisionalGap).where(AutomatedProvisionalGap.model_run_id == previous_run.id))
        return (saved, "reused") if saved is not None else (None, "already_attempted")
    # Finish all packet reads before any provider request. Persistence revalidates
    # the packet below, so a concurrent source change cannot become a gap.
    session.commit()
    job = session.get(ResearchJob, job_id)
    if job is None:
        raise ValueError("Unknown research job.")
    assignment = AgentModelAssignment.model_validate({**assignment.model_dump(), "prompt_template_version": VERSION})
    recorder = ModelRunRecorder()
    record = recorder.start(session, research_job=job, role=AgentRole.GAP_DETECTION, assignment=assignment,
                            input_reference=checkpoint)
    session.commit()
    metrics = None
    try:
        response, metrics = runner.generate(AgentRole.GAP_DETECTION, prompt_for(packet), ProvisionalGapResponse)
        response = ProvisionalGapResponse.model_validate(response)
        if response.abstained:
            recorder.succeed(record, output="ABSTAIN", metrics=metrics)
            record.model_config = {**record.model_config, "agent_outcome": "abstained"}
            session.commit()
            return None, "abstained"
        references = _resolve_references(response, packet)
        gap = persist(session, job_id=job_id, model_run_id=record.id, allowed_chunk_ids=allowed_chunk_ids, statement=response.statement,
                      rationale=response.rationale, uncertainty=response.uncertainty, references=references)
    except Exception as error:
        record = session.get(ModelRun, record.id)
        if isinstance(error, CloudInferenceError):
            recorder.fail(record, error)
        else:
            diagnostics = metrics.diagnostics if metrics is not None else None
            recorder.fail(record, CloudResponseError("invalid_gap_response", diagnostics))
        session.commit()
        return None, "failed"
    recorder.succeed(record, output=response.model_dump_json(), metrics=metrics)
    record.model_config = {**record.model_config, "agent_outcome": "generated"}
    session.commit()
    return gap, "generated"

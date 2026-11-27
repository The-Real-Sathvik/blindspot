"""Strict, source-bound automated assessment; never a human review."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import AutomatedSemanticAssessment, ExtractedClaim, ModelRun, ResearchJob
from app.schemas.research import AgentRole, SemanticSupportStatus
from app.services.cloud_inference import CloudInferenceError, CloudResponseError
from app.services.human_semantic_review import evidence_snapshot
from app.services.model_config import AgentModelAssignment
from app.services.model_runs import ModelRunRecorder, safe_hash
from app.services.web_claims import ensure_processed_source

VERSION = "automated-semantic-assessment-v2"
_PLAIN_TEXT_RESPONSE = re.compile(
    r"\ASTATUS: (?P<status>supported|partially_supported|contradicted|insufficient_evidence)\r?\n"
    r"RATIONALE: (?P<rationale>[^\r\n]+)\r?\n"
    r"UNCERTAINTY: (?P<uncertainty>[^\r\n]+)\Z"
)


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_status: SemanticSupportStatus
    rationale: str = Field(min_length=1, max_length=1600)
    uncertainty: str | None = Field(default=None, max_length=1600)


@dataclass(frozen=True)
class AssessmentResult:
    model_run_id: str | None
    outcome: str
    assessment_id: str | None = None
    reused: bool = False


def eligibility(claim: ExtractedClaim) -> str:
    """Validate the current exact evidence before any model sees it."""
    snapshot = evidence_snapshot(claim)
    for link in claim.evidence_links:
        ensure_processed_source(link.evidence_chunk, link.evidence_chunk.source_document)
    return snapshot


def parse_gemini_assessment_response(content, schema: type[BaseModel], diagnostics: dict) -> BaseModel:
    if schema is not AssessmentResponse:
        raise CloudResponseError("unsupported_plain_text_schema", diagnostics)
    if not isinstance(content, str):
        raise CloudResponseError("missing_response_content", diagnostics)
    if content == "ABSTAIN":
        diagnostics["plain_text_contract"] = "valid"
        return AssessmentResponse(
            assessment_status=SemanticSupportStatus.INSUFFICIENT_EVIDENCE,
            rationale="The model abstained from assessing this claim.",
            uncertainty="No automated assessment was produced.",
        )
    match = _PLAIN_TEXT_RESPONSE.fullmatch(content)
    if match is None:
        diagnostics["plain_text_contract"] = "invalid"
        raise CloudResponseError("invalid_plain_text_contract", diagnostics)
    diagnostics["plain_text_contract"] = "valid"
    return AssessmentResponse(
        assessment_status=match.group("status"),
        rationale=match.group("rationale"),
        uncertainty=None if match.group("uncertainty") == "NONE" else match.group("uncertainty"),
    )


def prompt_for(claim: ExtractedClaim, snapshot: str) -> str:
    evidence = [{"evidence_id": link.id, "quotation": link.quotation_text,
                 "page": link.evidence_chunk.page_number} for link in claim.evidence_links]
    return ("Assess only whether the claim is supported by the supplied exact quotations. "
            "Do not infer beyond them. Use supported only when the quotation directly supports the whole claim; "
            "use partially_supported when it supports only part, contradicted when it directly conflicts, "
            "and insufficient_evidence when support is not established. Do not use any other status. "
            "Return exactly these three lines and nothing else:\n"
            "STATUS: <supported|partially_supported|contradicted|insufficient_evidence>\n"
            "RATIONALE: <one concise sentence>\n"
            "UNCERTAINTY: <one concise sentence or NONE>\n"
            "If you cannot assess the claim, return exactly ABSTAIN. "
            "This is an automated assessment, not human review or scientific proof.\n"
            + json.dumps({"claim": claim.claim_text, "evidence_snapshot": snapshot, "evidence": evidence}, ensure_ascii=True, separators=(",", ":")))


def assess(session: Session, *, job_id: str, claim_id: str, assignment: AgentModelAssignment, runner) -> AssessmentResult:
    if session.bind is None or session.bind.dialect.name != "sqlite" or session.in_transaction():
        raise ValueError("Automated assessment requires a fresh SQLite session.")
    job = session.get(ResearchJob, job_id)
    claim = session.get(ExtractedClaim, claim_id)
    if job is None or claim is None or claim.model_run_id is None:
        raise ValueError("Unknown job or claim.")
    claim_run = session.get(ModelRun, claim.model_run_id)
    if claim_run is None or claim_run.research_job_id != job.id:
        raise ValueError("Claim is not associated with this research job.")
    snapshot = eligibility(claim)
    previous = session.scalar(select(AutomatedSemanticAssessment).where(
        AutomatedSemanticAssessment.claim_id == claim.id,
        AutomatedSemanticAssessment.evidence_snapshot == snapshot,
        AutomatedSemanticAssessment.assessment_version == VERSION,
    ))
    if previous:
        return AssessmentResult(previous.model_run_id, "assessed", previous.id, reused=True)
    input_reference = f"{claim.id}:{snapshot}:{VERSION}"
    prior_run = session.scalar(select(ModelRun).where(
        ModelRun.research_job_id == job.id,
        ModelRun.agent_role == AgentRole.SEMANTIC_REVIEW,
        ModelRun.prompt_template_version == VERSION,
        ModelRun.input_reference_hash == safe_hash(input_reference),
    ).order_by(ModelRun.created_at.desc()).limit(1))
    if prior_run is not None:
        return AssessmentResult(prior_run.id, "failed", reused=True)
    assignment = AgentModelAssignment.model_validate({**assignment.model_dump(), "prompt_template_version": VERSION})
    recorder = ModelRunRecorder()
    run = recorder.start(session, research_job=job, role=AgentRole.SEMANTIC_REVIEW, assignment=assignment, input_reference=input_reference)
    # Never retain a SQLite write transaction across provider execution.
    session.commit()
    try:
        response, metrics = runner.generate(AgentRole.SEMANTIC_REVIEW, prompt_for(claim, snapshot), AssessmentResponse)
        response = AssessmentResponse.model_validate(response)
        claim = session.get(ExtractedClaim, claim_id)
        if claim is None or eligibility(claim) != snapshot:
            raise RuntimeError("Source evidence changed before assessment persistence.")
    except Exception as error:
        run = session.get(ModelRun, run.id)
        recorder.fail(run, error if isinstance(error, CloudInferenceError) else RuntimeError("Assessment response validation failed."))
        session.commit()
        return AssessmentResult(run.id, "failed")
    row = AutomatedSemanticAssessment(claim=claim, model_run_id=run.id, evidence_snapshot=snapshot,
        assessment_status=response.assessment_status, rationale=response.rationale, uncertainty=response.uncertainty,
        assessment_version=VERSION, assessed_at=datetime.now(timezone.utc))
    session.add(row)
    recorder.succeed(run, output=response.model_dump_json(), metrics=metrics)
    session.commit()
    return AssessmentResult(run.id, "assessed", row.id)

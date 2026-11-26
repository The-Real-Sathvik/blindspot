"""Authoritative, deterministic job projection for the autonomous workspace."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.research import (AutomatedProvisionalGap, AutomatedSemanticAssessment, EvidenceChunk,
    ExtractedClaim, ModelRun, ResearchJobPaper, SourceDocument)
from app.schemas.research import AgentRole, SemanticSupportStatus


def synchronize_finalized_analysis_set(session: Session, job_id: str) -> list[str]:
    """Safely derive missing legacy selections from job-owned persisted evidence only."""
    links = session.scalars(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job_id)).all()
    selected = []
    for link in links:
        has_evidence = bool(session.scalar(select(EvidenceChunk.id).join(SourceDocument).where(
            SourceDocument.paper_id == link.paper_id).limit(1)))
        # Never select an unavailable candidate; no cross-paper substitution.
        if has_evidence:
            link.analysis_selected = True
            selected.append(link.paper_id)
    if selected:
        session.commit()
    return sorted(selected)


def project(session: Session, job_id: str) -> dict:
    selected = synchronize_finalized_analysis_set(session, job_id)
    links = session.scalars(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job_id)).all()
    selected_set = set(selected)
    candidates = len(links)
    evidence_chunks = session.scalar(select(func.count(EvidenceChunk.id)).join(SourceDocument).join(
        ResearchJobPaper, ResearchJobPaper.paper_id == SourceDocument.paper_id).where(
        ResearchJobPaper.research_job_id == job_id, ResearchJobPaper.analysis_selected.is_(True))) or 0
    claims = session.scalars(select(ExtractedClaim).join(ModelRun).join(
        ResearchJobPaper, ResearchJobPaper.paper_id == ExtractedClaim.paper_id).where(
        ModelRun.research_job_id == job_id, ResearchJobPaper.research_job_id == job_id,
        ResearchJobPaper.analysis_selected.is_(True))).all()
    assessments = session.scalars(select(AutomatedSemanticAssessment).join(ExtractedClaim).join(ModelRun).where(
        ModelRun.research_job_id == job_id)).all()
    latest = {}
    for item in sorted(assessments, key=lambda value: value.assessed_at, reverse=True):
        latest.setdefault(item.claim_id, item)
    status_counts = {status.value: 0 for status in SemanticSupportStatus}
    for item in latest.values(): status_counts[item.assessment_status.value] += 1
    supported_papers = {claim.paper_id for claim in claims if latest.get(claim.id) and latest[claim.id].assessment_status is SemanticSupportStatus.SUPPORTED}
    gaps = session.scalar(select(func.count(AutomatedProvisionalGap.id)).where(AutomatedProvisionalGap.research_job_id == job_id)) or 0
    semantic_runs = session.scalar(select(func.count(ModelRun.id)).where(ModelRun.research_job_id == job_id, ModelRun.agent_role == AgentRole.SEMANTIC_REVIEW)) or 0
    gap_runs = session.scalar(select(func.count(ModelRun.id)).where(ModelRun.research_job_id == job_id, ModelRun.agent_role == AgentRole.GAP_DETECTION)) or 0
    if gaps:
        gap_reason = None
    elif not claims:
        gap_reason = "No source-grounded findings were saved for the finalized analysis papers."
    elif not latest:
        gap_reason = "Automated semantic assessment has not run for the saved findings (legacy run); no gap analysis was attempted."
    elif len(supported_papers) < 2:
        gap_reason = f"Only {len(supported_papers)} finalized paper has supported automated findings; two distinct papers are required."
    elif not gap_runs:
        gap_reason = "Eligible automated findings exist, but provisional gap detection has not run."
    else:
        gap_reason = "No provisional gap passed the persisted validation gate."
    preparation = {"processing": "finished" if candidates else "pending", "coverage": "sufficient" if selected else "unavailable",
                   "detail": f"{candidates} candidates discovered; {len(selected)} verified analysis-ready papers finalized."}
    evidence = {"processing": "finished" if selected else "blocked", "coverage": "sufficient" if evidence_chunks else "unavailable",
                "detail": f"{evidence_chunks} page-linked evidence chunks across {len(selected)} finalized papers."}
    findings = {"processing": "finished" if claims else ("blocked" if not selected else "pending"),
                "coverage": "sufficient" if claims and latest else ("limited" if claims else "unavailable"),
                "detail": (f"{len(claims)} source-grounded findings; {len(latest)} automatically assessed." if claims else "No source-grounded findings were saved.")}
    gaps_state = {"processing": "finished" if gaps or gap_runs else "blocked", "coverage": "sufficient" if gaps else "limited", "detail": gap_reason}
    return {"analysis_paper_ids": selected, "candidate_count": candidates, "unavailable_candidate_count": candidates-len(selected),
            "evidence_chunk_count": evidence_chunks, "claim_count": len(claims), "assessment_count": len(latest),
            "assessment_statuses": status_counts, "supported_paper_count": len(supported_papers), "provisional_gap_count": gaps,
            "legacy": bool(claims and not semantic_runs), "stages": {"preparation": preparation, "evidence": evidence, "findings": findings, "gaps": gaps_state}}

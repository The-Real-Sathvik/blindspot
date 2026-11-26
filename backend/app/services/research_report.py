"""Deterministic, evidence-linked Markdown reports for saved research jobs."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import CandidateGap, ExtractedClaim, ModelRun, ResearchJob, ResearchJobPaper
from app.schemas.research import AgentRole
from app.services.human_semantic_review import current_human_review


def _line(value: str | None) -> str:
    return " ".join(str(value or "Not provided.").replace("\r", " ").replace("\n", " ").split())


def build_research_report(session: Session, job_id: str) -> str:
    """Render only persisted artifacts; absence is stated rather than filled in."""
    job = session.get(ResearchJob, job_id)
    if job is None:
        raise ValueError("Research job not found.")
    links = session.scalars(
        select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job.id)
        .order_by(ResearchJobPaper.included.desc(), ResearchJobPaper.relevance_score.desc(), ResearchJobPaper.paper_id)
    ).all()
    claims = session.scalars(
        select(ExtractedClaim).join(ModelRun, ExtractedClaim.model_run_id == ModelRun.id)
        .where(ModelRun.research_job_id == job.id, ModelRun.agent_role == AgentRole.CLAIM_EXTRACTION)
        .order_by(ExtractedClaim.paper_id, ExtractedClaim.created_at, ExtractedClaim.id)
    ).all()
    gaps = session.scalars(
        select(CandidateGap).where(CandidateGap.research_job_id == job.id)
        .order_by(CandidateGap.created_at, CandidateGap.id)
    ).all()

    out = ["# Blindspot research report", "", "## Research topic", "", _line(job.topic), "", "## Included papers", ""]
    included = [link for link in links if link.included is True]
    if not included:
        out.append("No papers are currently in the saved shortlist.")
    for link in included:
        paper = link.paper
        out.extend([f"- **{_line(paper.title)}** ({paper.publication_year or 'year not provided'})",
                    f"  - Selection: {_line(link.decision_reason)}",
                    f"  - DOI: {_line(paper.doi)}"])

    out.extend(["", "## Preliminary abstract metadata", ""])
    abstracts = [link.paper for link in included if isinstance(link.paper.abstract, str) and link.paper.abstract.strip()]
    if not abstracts:
        out.append("No saved abstracts are available for the included papers.")
    else:
        out.append("Saved abstracts are preliminary metadata only: they have no page citations and do not enter the full-text claim or gap gates.")
        for paper in abstracts:
            out.append(f"- {_line(paper.title)} ({'OpenAlex' if paper.openalex_id else 'arXiv' if paper.arxiv_id else 'saved source'}): {_line(paper.abstract)}")

    out.extend(["", "## Evidence-linked claims", ""])
    if not claims:
        out.append("No saved source-grounded claims are available. Claim extraction may not have been run, may have abstained, or may have failed structural validation.")
    contradictions = []
    for claim in claims:
        review = current_human_review(claim)
        status = claim.verification_result.semantic_status.value if review else "unverified"
        out.extend([f"### {_line(claim.paper.title)} — {_line(claim.claim_type.value)}",
                    _line(claim.claim_text),
                    f"- Scientific support: {status}",
                    f"- Human review: {_line(review.get('reviewer') if review else None)}",
                    f"- Review rationale: {_line(review.get('rationale') if review else 'No current human review.')}",
                    f"- Uncertainty: {_line(claim.uncertainty_or_missing_information)}"])
        for evidence in sorted(claim.evidence_links, key=lambda item: item.id):
            out.extend([f"> {_line(evidence.quotation_text)}",
                        f"> PDF page {evidence.evidence_chunk.page_number}; chunk {evidence.evidence_chunk_id}; offsets {evidence.quote_start_offset}–{evidence.quote_end_offset}; literal/source-location checks: {evidence.quotation_status.value}/{evidence.source_location_status.value}"])
        if status == "contradicted":
            contradictions.append(claim)

    out.extend(["", "## Candidate research gaps", ""])
    if not gaps:
        out.append("No candidate gaps are available. Gap detection requires current human-supported claims from at least two distinct papers and remains provisional even when candidates are saved.")
    for gap in gaps:
        out.extend([f"### {_line(gap.statement)}", f"- Category: {_line(gap.category)}", f"- Validation status: {_line(gap.validation_status)}", f"- Explanation: {_line(gap.explanation)}", f"- Counterevidence: {_line(gap.counterevidence)}", f"- Uncertainty: {_line(gap.uncertainty)}"])
        for reference in sorted(gap.references, key=lambda item: (item.role, item.paper_id, item.claim_id, item.claim_evidence_id)):
            out.append(f"- {reference.role}: paper {reference.paper_id}; claim {reference.claim_id}; evidence {reference.claim_evidence_id}")

    out.extend(["", "## Conflicting evidence", ""])
    if not contradictions:
        out.append("No current human-reviewed contradicted claims are saved for this job.")
    else:
        for claim in contradictions:
            out.append(f"- {_line(claim.paper.title)}: {_line(claim.claim_text)}")
    out.extend(["", "## Limitations and validation status", "", "- Literal quotation/source-location checks are distinct from scientific semantic support.", "- Human reviews are self-reported local assessments, not authenticated expert certification.", "- Candidate gaps are provisional and do not establish global novelty or absence of prior work.", "- This report is generated solely from stored job records; it does not add findings or infer missing evidence.", ""])
    return "\n".join(out)

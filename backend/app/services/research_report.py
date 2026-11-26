"""Deterministic, evidence-linked Markdown reports for saved research jobs."""
from __future__ import annotations

import re
import textwrap

import pymupdf as fitz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import (AutomatedProvisionalGap, AutomatedSemanticAssessment, CandidateGap,
    ExtractedClaim, ModelRun, ResearchJob, ResearchJobPaper)
from app.schemas.research import AgentRole
from app.services.human_semantic_review import current_human_review
from app.services.job_state import project as project_job_state


def _line(value: str | None) -> str:
    return " ".join(str(value or "Not provided.").replace("\r", " ").replace("\n", " ").split())


def build_research_report(session: Session, job_id: str) -> str:
    """Render only persisted artifacts; absence is stated rather than filled in."""
    workflow = project_job_state(session, job_id)
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
    automated_gaps = session.scalars(select(AutomatedProvisionalGap).where(
        AutomatedProvisionalGap.research_job_id == job.id).order_by(AutomatedProvisionalGap.created_at, AutomatedProvisionalGap.id)).all()
    automated_assessments = {item.claim_id: item for item in session.scalars(
        select(AutomatedSemanticAssessment).join(ExtractedClaim).join(ModelRun, ExtractedClaim.model_run_id == ModelRun.id)
        .where(ModelRun.research_job_id == job.id).order_by(AutomatedSemanticAssessment.assessed_at.desc())).all()}

    out = ["# Blindspot research report", "", "## Research topic", "", _line(job.topic), "", "## Executive summary", "", f"{workflow['candidate_count']} candidates were discovered; {len(workflow['analysis_paper_ids'])} papers with verified page-linked evidence were analyzed.", "", "## Analyzed papers", ""]
    included = [link for link in links if link.analysis_selected]
    if not included:
        out.append("No source-verified analysis papers are available; discovery candidates were not treated as analyzed.")
    for link in included:
        paper = link.paper
        out.extend([f"- **{_line(paper.title)}** ({paper.publication_year or 'year not provided'})",
                    f"  - Selection: {_line(link.decision_reason)}",
                    f"  - DOI: {_line(paper.doi)}"])

    unselected = [link for link in links if not link.analysis_selected]
    if unselected:
        out.extend(["", "## Discovery candidates not analyzed", ""])
        out.extend(f"- {_line(link.paper.title)} — no verified full-text evidence was selected for this report." for link in unselected)

    out.extend(["", "## Preliminary abstract metadata", ""])
    # Candidate abstracts remain clearly preliminary even when no paper reached
    # the finalized full-text analysis set.
    abstracts = [link.paper for link in links if isinstance(link.paper.abstract, str) and link.paper.abstract.strip()]
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
        automated = automated_assessments.get(claim.id)
        status = claim.verification_result.semantic_status.value if review else "unverified"
        out.extend([f"### {_line(claim.paper.title)} — {_line(claim.claim_type.value)}",
                    _line(claim.claim_text),
                    f"- Scientific support: {status}",
                    f"- Automated semantic assessment: {_line(automated.assessment_status.value if automated else 'not assessed')} (not human review)",
                    f"- Automated uncertainty: {_line(automated.uncertainty if automated else 'No automated assessment.')} ",
                    f"- Human review: {_line(review.get('reviewer') if review else None)}",
                    f"- Review rationale: {_line(review.get('rationale') if review else 'No current human review.')}",
                    f"- Uncertainty: {_line(claim.uncertainty_or_missing_information)}"])
        for evidence in sorted(claim.evidence_links, key=lambda item: item.id):
            out.extend([f"> {_line(evidence.quotation_text)}",
                        f"> PDF page {evidence.evidence_chunk.page_number}; chunk {evidence.evidence_chunk_id}; offsets {evidence.quote_start_offset}–{evidence.quote_end_offset}; literal/source-location checks: {evidence.quotation_status.value}/{evidence.source_location_status.value}"])
        if status == "contradicted":
            contradictions.append(claim)

    out.extend(["", "## Provisional research gaps", ""])
    if not gaps:
        out.append("No candidate gaps are available. " + workflow["stages"]["gaps"]["detail"])
    for gap in gaps:
        out.extend([f"### {_line(gap.statement)}", f"- Category: {_line(gap.category)}", f"- Validation status: {_line(gap.validation_status)}", f"- Explanation: {_line(gap.explanation)}", f"- Counterevidence: {_line(gap.counterevidence)}", f"- Uncertainty: {_line(gap.uncertainty)}"])
        for reference in sorted(gap.references, key=lambda item: (item.role, item.paper_id, item.claim_id, item.claim_evidence_id)):
            out.append(f"- {reference.role}: paper {reference.paper_id}; claim {reference.claim_id}; evidence {reference.claim_evidence_id}")
    for gap in automated_gaps:
        out.extend([f"### {_line(gap.statement)}", "- Validation status: Provisional — Not globally validated.",
                    f"- Automated rationale: {_line(gap.rationale)}", f"- Uncertainty: {_line(gap.uncertainty)}"])
        for reference in sorted(gap.references, key=lambda item: (item.role, item.paper_id, item.claim_id)):
            out.append(f"- {reference.role}: paper {reference.paper_id}; claim {reference.claim_id}; evidence {reference.claim_evidence_id}")

    out.extend(["", "## Conflicting evidence", ""])
    if not contradictions:
        out.append("No current human-reviewed contradicted claims are saved for this job.")
    else:
        for claim in contradictions:
            out.append(f"- {_line(claim.paper.title)}: {_line(claim.claim_text)}")
    out.extend(["", "## Limitations and validation status", "", "- Literal quotation/source-location checks are distinct from automated semantic assessment.", "- Automated assessments are not human review or independent scientific proof.", "- Provisional gaps do not establish global novelty or absence of prior work.", "- This report is generated solely from stored job records; it does not add findings or infer missing evidence.", ""])
    return "\n".join(out)


def render_research_report_pdf(session: Session, job_id: str) -> bytes:
    """Render the stored, deterministic report without adding research content."""
    report = build_research_report(session, job_id)
    pdf = fitz.open()
    page = None
    y = 0.0
    margin, width, bottom = 54, 487, 780
    def new_page():
        nonlocal page, y
        page = pdf.new_page(width=595, height=842)
        y = margin
        page.insert_text((margin, y), "BLINDSPOT  ·  EVIDENCE-GROUNDED RESEARCH", fontsize=8, color=(0.4, 0.4, 0.4))
        y += 26
    def write(value, *, size=10, bold=False):
        nonlocal y
        lines = textwrap.wrap(value, width=max(24, int(92 * 10 / size)), break_long_words=False) or [""]
        needed = len(lines) * (size + 4) + 6
        if page is None or y + needed > bottom: new_page()
        for line in lines:
            page.insert_text((margin, y), line, fontsize=size, fontname="hebo" if bold else "helv", color=(0.08, 0.08, 0.08))
            y += size + 4
        y += 4
    for raw in report.splitlines():
        line = raw.strip()
        if not line: y += 3; continue
        if line.startswith("# "): write(line[2:], size=18, bold=True)
        elif line.startswith("## "): write(line[3:], size=13, bold=True)
        elif line.startswith("### "): write(line[4:], size=11, bold=True)
        else: write(re.sub(r"^[>-]\s*", "", line).replace("**", ""), size=10)
    for index, item in enumerate(pdf, start=1):
        item.insert_text((margin, 812), f"Blindspot research report  ·  {index} / {len(pdf)}", fontsize=8, color=(0.4, 0.4, 0.4))
    value = pdf.tobytes(garbage=4, deflate=True)
    pdf.close()
    return value

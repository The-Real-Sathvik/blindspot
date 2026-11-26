"""Bounded, restart-safe orchestration of already-approved local research stages."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import ResearchJob, ResearchJobPaper, SearchRun
from app.schemas.research import JobStatus
from app.services.document_attempts import job_document_state, perform_attempt


@dataclass(frozen=True)
class AutonomousRunResult:
    outcome: str
    discovery: str
    processed: int
    evidence_ready: int
    failed_documents: int
    abstract_available: int
    limitation: str | None = None


class AutonomousResearchOrchestrator:
    """Runs only persisted, bounded stages. It deliberately does not invoke AI agents."""
    def __init__(self, discovery_factory, *, downloader_factory=None, shortlist_limit: int = 5):
        self.discovery_factory = discovery_factory
        self.downloader_factory = downloader_factory
        self.shortlist_limit = shortlist_limit

    def run(self, session: Session, job: ResearchJob) -> AutonomousRunResult:
        job.status = JobStatus.PROCESSING
        session.commit()  # accepted work survives a process interruption
        discovery = "reused"
        if not session.scalar(select(SearchRun.id).where(SearchRun.research_job_id == job.id).limit(1)):
            try:
                result = self.discovery_factory().discover(session, job, total_budget=30, shortlist_size=self.shortlist_limit)
                session.commit()
                discovery = "completed"
            except Exception:
                session.rollback()
                job = session.get(ResearchJob, job.id)
                job.status, job.error_info = JobStatus.RETRIABLE_ERROR, "Automatic paper discovery could not be completed. Existing saved work was preserved."
                session.commit()
                return AutonomousRunResult("blocked", "failed", 0, 0, 0, 0, "Paper discovery is unavailable; retry this job later.")

        links = session.scalars(select(ResearchJobPaper).where(
            ResearchJobPaper.research_job_id == job.id, ResearchJobPaper.included.is_(True)
        ).order_by(ResearchJobPaper.relevance_score.desc(), ResearchJobPaper.paper_id).limit(self.shortlist_limit)).all()
        processed = evidence_ready = failed = 0
        for link in links:
            paper = link.paper
            state = job_document_state(session, job.id, paper)
            if state["status"] == "extracted":
                evidence_ready += 1
                continue
            # A permanent failure remains a checkpoint; do not turn it into an automatic retry.
            if state.get("attempt", {}).get("outcome") == "failed" and state["attempt"].get("retryable") is False:
                failed += 1
                continue
            if state.get("can_acquire"):
                state = perform_attempt(session, job.id, paper, "acquire", downloader_factory=self.downloader_factory)
            if state.get("can_extract"):
                state = perform_attempt(session, job.id, paper, "extract")
            processed += 1
            if state["status"] == "extracted": evidence_ready += 1
            elif state["status"] == "failed": failed += 1

        abstract_available = sum(bool(link.paper.abstract and link.paper.abstract.strip()) for link in links)
        job = session.get(ResearchJob, job.id)
        if evidence_ready:
            job.status, job.error_info = JobStatus.COMPLETED, None
            outcome, limitation = "complete", "Claim extraction and semantic review remain unavailable until separately configured and explicitly approved."
        else:
            job.status = JobStatus.COMPLETED
            job.error_info = "Automatic collection completed with insufficient full-text evidence. The report records this limitation."
            outcome, limitation = "insufficient_evidence", "No page-linked full-text evidence was prepared; abstracts remain preliminary metadata only."
        session.commit()
        return AutonomousRunResult(outcome, discovery, processed, evidence_ready, failed, abstract_available, limitation)

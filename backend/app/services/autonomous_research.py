"""Bounded, restart-safe orchestration of already-approved local research stages."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import EvidenceChunk, ModelRun, ResearchJob, ResearchJobPaper, SearchRun, SourceDocument
from app.schemas.research import JobStatus
from app.schemas.research import AgentRole
from app.services.claim_extraction_v3 import NoAdmissiblePassage, PROMPT_VERSION, run_development_extraction
from app.services.document_attempts import job_document_state, perform_attempt
from app.services.web_claims import ensure_processed_source


@dataclass(frozen=True)
class AutonomousRunResult:
    outcome: str
    discovery: str
    processed: int
    evidence_ready: int
    failed_documents: int
    abstract_available: int
    claim_attempted: int = 0
    claim_accepted: int = 0
    claim_abstained: int = 0
    claim_failed: int = 0
    limitation: str | None = None


class AutonomousResearchOrchestrator:
    """Runs persisted stages sequentially; optional V3 execution is explicitly consent-gated by the API."""
    def __init__(self, discovery_factory, *, downloader_factory=None, claim_runner=None, claim_assignment=None,
                 shortlist_limit: int = 5, max_claim_papers: int = 3, max_recovery_papers: int = 3):
        self.discovery_factory = discovery_factory
        self.downloader_factory = downloader_factory
        self.claim_runner = claim_runner
        self.claim_assignment = claim_assignment
        self.shortlist_limit = shortlist_limit
        self.max_claim_papers = max_claim_papers
        self.max_recovery_papers = max_recovery_papers

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
                return AutonomousRunResult("blocked", "failed", 0, 0, 0, 0,
                                           limitation="Paper discovery is unavailable; retry this job later.")

        links = session.scalars(select(ResearchJobPaper).where(
            ResearchJobPaper.research_job_id == job.id, ResearchJobPaper.included.is_(True)
        ).order_by(ResearchJobPaper.relevance_score.desc(), ResearchJobPaper.paper_id).limit(self.shortlist_limit)).all()
        processed = evidence_ready = failed = 0

        def prepare(link):
            """Use one persisted source checkpoint; never retry a failed automatic attempt."""
            nonlocal processed, evidence_ready, failed
            paper = link.paper
            state = job_document_state(session, job.id, paper)
            if state["status"] == "extracted":
                evidence_ready += 1
                return state
            # An automatic run never retries a completed failed attempt. A later,
            # explicit recovery can use its retryability metadata instead.
            if state.get("attempt", {}).get("outcome") == "failed":
                failed += 1
                return state
            if state.get("can_acquire"):
                state = perform_attempt(session, job.id, paper, "acquire", downloader_factory=self.downloader_factory)
            if state.get("can_extract"):
                state = perform_attempt(session, job.id, paper, "extract")
            processed += 1
            if state["status"] == "extracted": evidence_ready += 1
            elif state["status"] == "failed": failed += 1
            return state

        for link in links:
            prepare(link)

        abstract_available = sum(bool(link.paper.abstract and link.paper.abstract.strip()) for link in links)
        # The visible shortlist remains unchanged. If it yielded no usable
        # evidence, recover through a bounded set of job-associated alternatives.
        # Source readiness ranks ahead of relevance only after the shortlist has
        # proved unusable: saved evidence, saved PDF, then approved PDF URL.
        claim_links = list(links)
        if evidence_ready == 0:
            alternatives = session.scalars(select(ResearchJobPaper).where(
                ResearchJobPaper.research_job_id == job.id, ResearchJobPaper.included.is_not(True)
            ).order_by(ResearchJobPaper.relevance_score.desc(), ResearchJobPaper.paper_id)).all()

            def readiness(link):
                state = job_document_state(session, job.id, link.paper)
                if state["status"] == "extracted": return 0
                if state.get("can_extract"): return 1
                if state.get("can_acquire"): return 2
                return 3

            recovery = sorted(alternatives, key=lambda link: (readiness(link), -(link.relevance_score or 0), link.paper_id))
            for alternative in recovery[:self.max_recovery_papers]:
                state = prepare(alternative)
                if state["status"] == "extracted":
                    claim_links.append(alternative)
                    # One source-verified alternative is sufficient to begin
                    # bounded claim extraction. Do not download further PDFs
                    # merely because recovery candidates remain available.
                    break

        claim_attempted = claim_accepted = claim_abstained = claim_failed = 0
        if self.claim_runner is not None and self.claim_assignment is not None:
            for link in claim_links:
                if claim_attempted >= self.max_claim_papers:
                    break
                # Checkpoint every terminal per-paper attempt. Failed calls are
                # not retried automatically on a later autonomous run.
                checkpoint = link.error_info or ""
                # Older autonomous runs collapsed the fresh-session preflight
                # error into this source-validation marker before ModelRun
                # creation. Re-evaluate that one legacy marker once; all new,
                # specific terminal checkpoints remain non-retryable.
                if checkpoint.startswith("[claim-extraction]") and checkpoint != "[claim-extraction] source validation blocked automatic analysis.":
                    continue
                document = session.scalar(select(SourceDocument).where(
                    SourceDocument.paper_id == link.paper_id
                ).order_by(SourceDocument.created_at, SourceDocument.id).limit(1))
                if document is None:
                    continue
                chunks = session.scalars(select(EvidenceChunk).where(
                    EvidenceChunk.source_document_id == document.id
                ).order_by(EvidenceChunk.page_number, EvidenceChunk.chunk_index, EvidenceChunk.id)).all()
                result = None
                for chunk in chunks:
                    try:
                        # V3 owns its SQLite transaction (BEGIN IMMEDIATE).
                        # Finish orchestration reads/writes before transferring
                        # transaction ownership, otherwise V3 rejects the call
                        # before it can record a ModelRun.
                        if session.in_transaction():
                            session.commit()
                        result = run_development_extraction(
                            session, job_id=job.id, chunk_id=chunk.id,
                            development_chunk_ids=frozenset([chunk.id]), assignment=self.claim_assignment,
                            runner=self.claim_runner, source_guard=ensure_processed_source,
                            select_complete_spans=True,
                        )
                    except NoAdmissiblePassage:
                        session.rollback()
                        continue
                    except Exception as error:
                        session.rollback()
                        link = session.get(ResearchJobPaper, link.id)
                        category = "source_validation_failed" if error.__class__.__name__ == "HTTPException" else "local_preflight_failed"
                        link.error_info = f"[claim-extraction] {category}; no automatic retry was made."
                        session.commit()
                        result = False
                    break
                if result is None:
                    continue
                if result is False:
                    claim_failed += 1
                    continue
                claim_attempted += 1
                run = session.get(ModelRun, result.model_run_id)
                link = session.get(ResearchJobPaper, link.id)
                if result.outcome == "claims_extracted":
                    claim_accepted += result.accepted_count
                    link.error_info = "[claim-extraction] completed with source-linked claims; semantic support remains unverified."
                elif result.outcome == "no_claims":
                    claim_abstained += 1
                    link.error_info = "[claim-extraction] abstained; no source-grounded claim was returned."
                else:
                    claim_failed += 1
                    category = run.error_category if run is not None and run.error_category else "failed_inference"
                    link.error_info = f"[claim-extraction] {category}; no automatic retry was made."
                session.commit()
        job = session.get(ResearchJob, job.id)
        if evidence_ready:
            job.status, job.error_info = JobStatus.COMPLETED, None
            outcome = "complete"
            limitation = ("Saved claim extraction ran only for bounded, source-verified evidence; "
                          "semantic assessment and gap detection still require separate safeguards.")
        else:
            job.status = JobStatus.COMPLETED
            job.error_info = "Automatic collection completed with insufficient full-text evidence. The report records this limitation."
            outcome, limitation = "insufficient_evidence", "No page-linked full-text evidence was prepared; abstracts remain preliminary metadata only."
        session.commit()
        return AutonomousRunResult(outcome, discovery, processed, evidence_ready, failed, abstract_available,
                                   claim_attempted, claim_accepted, claim_abstained, claim_failed, limitation)

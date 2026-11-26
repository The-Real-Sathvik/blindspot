"""Local research workspace API. Cloud inference requires explicit consent."""
from pathlib import Path
from hashlib import sha256
from uuid import uuid4
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.core.config import ConfigurationError, load_cloud_inference_settings, load_openalex_settings
from app.db.session import create_session_factory, create_sqlite_engine
from app.models.research import (AutomatedProvisionalGap, AutomatedSemanticAssessment, CandidateGap, ClaimEvidence,
    EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJob, ResearchJobPaper, SearchRun, SourceDocument)
from app.schemas.research import AgentRole, ResearchJobCreate
from app.services.discovery_quality import OpenAlexSourceAdapter, QualityDiscoveryService
from app.services.openalex import OpenAlexClient
from app.services.web_fulltext import PROCESSING_LOCK, acquire as acquire_document, document_state, extract as extract_document
from app.services.web_fulltext import safe_local_file
from app.services import web_fulltext
from app.services.document_attempts import job_document_state, perform_attempt
from app.services.web_claims import ensure_processed_source, heldout_document_ids
from app.services.human_semantic_review import HumanReviewRequest, current_human_review, record_human_review
from app.services.upload_identity import assess as assess_upload_identity, confirm as confirm_upload_identity
from app.services.claim_extraction_v3 import PROMPT_VERSION, NoAdmissiblePassage, run_development_extraction
from app.services.cloud_inference import CloudBudget, CloudInferenceRouter, GeminiClient, GroqClient, OpenRouterClient
from app.services.model_config import AgentModelAssignment, BlindspotModelConfig, InferenceProvider
from app.services.gap_detection import prepare_packet, run_gap_detection
from app.services.research_report import build_research_report, render_research_report_pdf
from app.services.autonomous_research import AutonomousResearchOrchestrator
from app.schemas.research import DocumentStatus

DB_PATH = Path(__file__).resolve().parents[1] / "blindspot.db"


class ClaimExtractionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chunk_id: str = Field(min_length=1, max_length=64)
    provider: Literal['gemini', 'groq', 'openrouter'] = 'gemini'
    cloud_consent: StrictBool


class AutonomousRunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cloud_consent: StrictBool


class GapDetectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cloud_consent: StrictBool


class IdentityConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm_association: StrictBool
    review_token: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


def create_app(session_factory=None, discovery_factory=None, fulltext_downloader_factory=None, claim_runner_factory=None, gap_runner_factory=None, orchestrator_factory=None):
    app = FastAPI(title="Blindspot local API")
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Blindspot-Request"])
    if session_factory is None:
        session_factory = create_session_factory(create_sqlite_engine(f"sqlite:///{DB_PATH.as_posix()}"))

    def db():
        with session_factory() as session:
            yield session

    def job_or_404(session, job_id):
        job = session.get(ResearchJob, job_id)
        if job is None: raise HTTPException(404, "Research job not found.", headers={'X-Blindspot-Error-Code':'job_not_found', 'X-Blindspot-Error-Stage':'request_validation'})
        return job

    def require_local_request(x_blindspot_request: str | None = Header(default=None)):
        if x_blindspot_request != "1":
            raise HTTPException(403, "Missing local request header.")

    def val(x): return x.value if hasattr(x, "value") else x

    def job_json(job):
        return {"id": job.id, "topic": job.topic, "status": val(job.status), "error_info": job.error_info, "created_at": job.created_at, "updated_at": job.updated_at}

    def paper_json(link):
        p = link.paper
        url = p.landing_page_url if p.landing_page_url and urlparse(p.landing_page_url).scheme in {"http", "https"} else None
        oa = p.open_access_metadata if isinstance(p.open_access_metadata, dict) else {}
        return {"id": p.id, "title": p.title, "authors": p.authors, "year": p.publication_year, "source": "OpenAlex + arXiv" if p.openalex_id and p.arxiv_id else ("OpenAlex" if p.openalex_id else ("arXiv" if p.arxiv_id else "Unknown")), "doi": p.doi, "abstract": p.abstract, "landing_page_url": url, "publication_type": p.publication_type, "included": link.included, "analysis_selected": link.analysis_selected, "score": link.relevance_score, "selection_reason": link.decision_reason, "status": val(link.status), "open_access": oa.get('is_oa') if isinstance(oa.get('is_oa'), bool) else None, "document": job_document_state(object_session(link), link.research_job_id, p)}

    @app.get("/api/health")
    def health(): return {"status": "ok"}

    @app.post("/api/jobs", status_code=201, dependencies=[Depends(require_local_request)])
    def create_job(body: ResearchJobCreate, session: Session = Depends(db)):
        job = ResearchJob(topic=body.topic); session.add(job); session.commit(); session.refresh(job)
        return job_json(job)

    @app.get("/api/jobs")
    def jobs(limit: Annotated[int, Query(ge=1, le=100)] = 25, session: Session = Depends(db)):
        return [job_json(j) for j in session.scalars(select(ResearchJob).order_by(ResearchJob.created_at.desc(), ResearchJob.id).limit(limit))]

    @app.get("/api/jobs/{job_id}")
    def job_details(job_id: str, session: Session = Depends(db)):
        job = job_or_404(session, job_id)
        searches = session.scalars(select(SearchRun).where(SearchRun.research_job_id == job_id).order_by(SearchRun.created_at.desc()).limit(20)).all()
        runs = session.scalars(select(ModelRun).where(ModelRun.research_job_id == job_id).order_by(ModelRun.created_at.desc()).limit(20)).all()
        papers = session.scalars(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job_id)).all()
        claim_count = session.scalar(select(func.count(ExtractedClaim.id)).join(ModelRun).where(ModelRun.research_job_id == job_id)) or 0
        assessment_count = session.scalar(select(func.count(AutomatedSemanticAssessment.id)).join(ExtractedClaim).join(ModelRun).where(ModelRun.research_job_id == job_id)) or 0
        auto_gap_count = session.scalar(select(func.count(AutomatedProvisionalGap.id)).where(AutomatedProvisionalGap.research_job_id == job_id)) or 0
        evidence_paper_ids = sorted(link.paper_id for link in papers if link.analysis_selected)
        return {**job_json(job), "paper_count": len(papers), "shortlist_count": sum(p.included is True for p in papers),
                "analysis_paper_ids": evidence_paper_ids, "searches": [{"source": r.source_name, "query": r.query_text, "retrieved": r.retrieved_count, "failed": bool(r.error_info)} for r in searches],
                "runs": [{"role": val(r.agent_role), "status": val(r.status), "outcome": val(r.outcome_status), "error_category": r.error_category} for r in runs], "warnings": ([job.error_info] if job.error_info else []),
                "stages": [{"name": "Research topic", "state": "complete"}, {"name": "Paper discovery & full-text preparation", "state": "complete" if evidence_paper_ids else ("in_progress" if searches else "not_started"), "detail": f"{len(papers)} candidates; {len(evidence_paper_ids)} analysis-ready papers."}, {"name": "Evidence", "state": "complete" if evidence_paper_ids else "blocked"}, {"name": "Findings", "state": "complete" if claim_count else "not_started"}, {"name": "Research gaps", "state": "complete" if auto_gap_count else ("insufficient_evidence" if assessment_count else "not_started"), "detail": "Requires supported automated assessments from two distinct selected papers; outputs are not globally validated."}]}

    @app.post("/api/jobs/{job_id}/run", dependencies=[Depends(require_local_request)])
    def run_autonomous_job(job_id: str, body: AutonomousRunRequest, session: Session = Depends(db)):
        job = job_or_404(session, job_id)
        if not body.cloud_consent:
            raise HTTPException(403, 'Explicit cloud-research consent is required.')
        if not PROCESSING_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Another local document workflow is in progress.")
        try:
            if orchestrator_factory:
                factory = orchestrator_factory
            else:
                settings = load_cloud_inference_settings()
                assignment = AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag='gemini-3.5-flash-lite',
                    output_tokens=512, gemini_response_mode='plain_text', prompt_template_version=PROMPT_VERSION)
                semantic_assignment = AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag='gemini-3.5-flash-lite',
                    output_tokens=512, gemini_response_mode='prompt_json', prompt_template_version='automated-semantic-assessment-v1')
                gap_assignment = AgentModelAssignment(provider=InferenceProvider.GEMINI, model_tag='gemini-3.5-flash-lite',
                    output_tokens=512, gemini_response_mode='prompt_json', prompt_template_version='automated-provisional-gap-v1')
                # One shared server-side budget covers all autonomous cloud roles:
                # three claims, three assessments, and at most two Agent 2 calls.
                budget = CloudBudget(max_requests=8, max_output_tokens_total=4096)
                runner = CloudInferenceRouter(BlindspotModelConfig(claim_extraction=assignment,
                    semantic_review=semantic_assignment, gap_detection=gap_assignment), settings,
                    gemini=GeminiClient(api_key=settings.gemini_api_key, budget=budget))
                factory = lambda: AutonomousResearchOrchestrator(
                    lambda: discovery_factory() if discovery_factory else QualityDiscoveryService(OpenAlexSourceAdapter(OpenAlexClient(load_openalex_settings())),),
                    downloader_factory=fulltext_downloader_factory, claim_runner=runner, claim_assignment=assignment,
                    semantic_assignment=semantic_assignment, gap_assignment=gap_assignment,
                    max_claim_papers=3, max_assessment_claims=3, max_gap_runs=2,
                )
            result = factory().run(session, job)
            return {"status": result.outcome, "discovery": result.discovery, "processed": result.processed,
                    "evidence_ready": result.evidence_ready, "failed_documents": result.failed_documents,
                    "abstract_available": result.abstract_available, "claim_attempted": result.claim_attempted,
                    "claim_accepted": result.claim_accepted, "claim_abstained": result.claim_abstained,
                    "claim_failed": result.claim_failed, "assessment_attempted": result.assessment_attempted,
                    "assessment_supported": result.assessment_supported, "assessment_failed": result.assessment_failed,
                    "provisional_gap_outcome": result.provisional_gap_outcome, "cloud_request_limit": 8,
                    "limitation": result.limitation}
        finally:
            PROCESSING_LOCK.release()

    @app.post("/api/jobs/{job_id}/discover", dependencies=[Depends(require_local_request)])
    def discover(job_id: str, session: Session = Depends(db)):
        job = job_or_404(session, job_id)
        try:
            service = discovery_factory() if discovery_factory else QualityDiscoveryService(OpenAlexSourceAdapter(OpenAlexClient(load_openalex_settings())))
            result = service.discover(session, job, total_budget=30, shortlist_size=5)
            session.commit()
            return {"status": "complete", "retrieved": result.retrieved_count, "duplicates": result.duplicate_count, "shortlisted": len(result.shortlisted)}
        except ConfigurationError:
            session.rollback(); raise HTTPException(503, "OpenAlex configuration is unavailable.") from None
        except Exception:
            session.rollback(); raise HTTPException(502, "Paper discovery failed. No partial results were saved.") from None

    @app.get("/api/jobs/{job_id}/papers")
    def papers(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        links = session.scalars(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job_id).order_by(ResearchJobPaper.relevance_score.desc(), ResearchJobPaper.id).limit(100)).all()
        return [paper_json(link) for link in links]

    @app.post("/api/jobs/{job_id}/prepare-shortlist", dependencies=[Depends(require_local_request)])
    def prepare_shortlist(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        if not PROCESSING_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Another document or extraction is being processed.")
        try:
            ids = session.scalars(select(ResearchJobPaper.paper_id).where(ResearchJobPaper.research_job_id == job_id, ResearchJobPaper.included.is_(True)).order_by(ResearchJobPaper.relevance_score.desc(), ResearchJobPaper.paper_id).limit(6)).all()
            results = []
            for paper_id in ids[:5]:
                try:
                    paper = session.get(Paper, paper_id)
                    if paper is None:
                        results.append({'paper_id': paper_id, 'status': 'failed', 'message': 'Paper is unavailable.'})
                        continue
                    state = job_document_state(session, job_id, paper)
                    if state['status'] == 'failed' or state.get('attempt', {}).get('outcome') == 'started':
                        results.append({'paper_id': paper_id, **state}); continue
                    if state['can_acquire']:
                        state = perform_attempt(session, job_id, paper, 'acquire', downloader_factory=fulltext_downloader_factory)
                    if state['can_extract']:
                        state = perform_attempt(session, job_id, paper, 'extract')
                    results.append({'paper_id': paper_id, **state})
                except Exception:
                    session.rollback()
                    results.append({'paper_id': paper_id, 'status': 'failed', 'message': 'Document processing could not be completed.'})
            return {'results': results, 'limit': 5, 'has_more': len(ids) > 5}
        finally:
            PROCESSING_LOCK.release()

    @app.get("/api/jobs/{job_id}/evidence")
    def evidence(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        docs = session.scalars(select(SourceDocument).join(ResearchJobPaper, ResearchJobPaper.paper_id == SourceDocument.paper_id).where(ResearchJobPaper.research_job_id == job_id, ResearchJobPaper.analysis_selected.is_(True)).order_by(SourceDocument.created_at.desc()).limit(100)).all()
        try: excluded = heldout_document_ids()
        except HTTPException: excluded = None
        heldout_hashes = set(session.scalars(select(SourceDocument.content_hash).where(SourceDocument.id.in_(excluded), SourceDocument.content_hash.is_not(None)))) if excluded is not None else set()
        def upload_ready(d):
            if d.source_url != 'user-upload:unverified': return True
            try: return d.content_hash not in heldout_hashes and assess_upload_identity(d)['status'] == 'human_confirmed'
            except Exception: return False
        return [{"id": d.id, "paper_id": d.paper_id, "paper_title": d.paper.title, "status": val(d.status), "origin": 'user_upload_unverified' if d.source_url == 'user-upload:unverified' else 'source_metadata', "has_local_file": bool(safe_local_file(d)), "claims_available": excluded is not None and d.id not in excluded and upload_ready(d) and d.document_type == 'pdf' and d.status == DocumentStatus.AVAILABLE and bool(safe_local_file(d)), "chunks": [{"id": c.id, "page": c.page_number, "text": c.text, "start_offset": c.start_offset, "end_offset": c.end_offset} for c in sorted(d.evidence_chunks, key=lambda x: x.chunk_index)[:100]]} for d in docs]

    def job_paper(session, job_id, paper_id):
        job_or_404(session, job_id)
        link = session.scalar(select(ResearchJobPaper).where(ResearchJobPaper.research_job_id == job_id, ResearchJobPaper.paper_id == paper_id))
        if link is None: raise HTTPException(404, "Paper is not associated with this research job.", headers={'X-Blindspot-Error-Code':'paper_not_found', 'X-Blindspot-Error-Stage':'request_validation'})
        return link.paper

    @app.get("/api/jobs/{job_id}/papers/{paper_id}/document")
    def paper_document(job_id: str, paper_id: str, session: Session = Depends(db)):
        return job_document_state(session, job_id, job_paper(session, job_id, paper_id))

    def uploaded_document(session, job_id, paper_id, document_id):
        paper = job_paper(session, job_id, paper_id)
        document = session.get(SourceDocument, document_id)
        if not document or document.paper_id != paper.id or document.source_url != 'user-upload:unverified':
            raise HTTPException(404, 'Uploaded document not found for this paper.')
        return document

    @app.get('/api/jobs/{job_id}/papers/{paper_id}/documents/{document_id}/identity')
    def identity_review(job_id: str, paper_id: str, document_id: str, session: Session = Depends(db)):
        document = uploaded_document(session, job_id, paper_id, document_id)
        try: return assess_upload_identity(document)
        except Exception: raise HTTPException(409, 'Document identity cannot be assessed safely.') from None

    @app.post('/api/jobs/{job_id}/papers/{paper_id}/documents/{document_id}/identity', dependencies=[Depends(require_local_request)])
    def identity_confirm(job_id: str, paper_id: str, document_id: str, body: IdentityConfirmation, session: Session = Depends(db)):
        if not body.confirm_association: raise HTTPException(403, 'Explicit human confirmation is required.')
        document = uploaded_document(session, job_id, paper_id, document_id)
        if not PROCESSING_LOCK.acquire(blocking=False): raise HTTPException(409, 'Another document operation is in progress.')
        try:
            result = confirm_upload_identity(session, document, job_id, body.review_token); session.commit(); return result
        except ValueError as error:
            session.rollback(); raise HTTPException(409, str(error)) from None
        except Exception:
            session.rollback(); raise HTTPException(409, 'Document identity cannot be assessed safely.') from None
        finally: PROCESSING_LOCK.release()

    @app.post("/api/jobs/{job_id}/papers/{paper_id}/acquire", dependencies=[Depends(require_local_request)])
    def acquire_paper(job_id: str, paper_id: str, session: Session = Depends(db)):
        paper = job_paper(session, job_id, paper_id)
        if not PROCESSING_LOCK.acquire(blocking=False): raise HTTPException(409, "Another document is being processed. Try again after it finishes.")
        try:
            return perform_attempt(session, job_id, paper, 'acquire', downloader_factory=fulltext_downloader_factory)
        except Exception:
            session.rollback(); raise HTTPException(500, "Document acquisition could not be completed.") from None
        finally: PROCESSING_LOCK.release()

    @app.post("/api/jobs/{job_id}/papers/{paper_id}/extract", dependencies=[Depends(require_local_request)])
    def extract_paper(job_id: str, paper_id: str, session: Session = Depends(db)):
        paper = job_paper(session, job_id, paper_id)
        if not PROCESSING_LOCK.acquire(blocking=False): raise HTTPException(409, "Another document is being processed. Try again after it finishes.")
        try:
            return perform_attempt(session, job_id, paper, 'extract')
        except Exception:
            session.rollback(); raise HTTPException(500, "Evidence extraction could not be completed.") from None
        finally: PROCESSING_LOCK.release()

    @app.post("/api/jobs/{job_id}/papers/{paper_id}/upload", dependencies=[Depends(require_local_request)])
    async def upload_paper(job_id: str, paper_id: str, request: Request, session: Session = Depends(db)):
        paper = job_paper(session, job_id, paper_id)
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/pdf':
            raise HTTPException(415, 'Send a PDF file with application/pdf content type.')
        if not PROCESSING_LOCK.acquire(blocking=False):
            raise HTTPException(409, 'Another document is being processed.')
        target = web_fulltext.SOURCE_DIR / f'upload-{uuid4().hex}.pdf'
        keep = False
        created = False
        try:
            web_fulltext.SOURCE_DIR.mkdir(parents=True, exist_ok=True)
            digest, total = sha256(), 0
            with target.open('xb') as output:
                created = True
                async for block in request.stream():
                    total += len(block)
                    if total > web_fulltext.MAX_PDF_BYTES:
                        raise HTTPException(413, 'PDF exceeds the 20 MB limit.')
                    digest.update(block); output.write(block)
            web_fulltext.validate_upload(target)
            state, reused = web_fulltext.save_upload(session, paper, target, digest.hexdigest())
            session.commit()
            keep = not reused
            return {**state, 'reused': reused}
        except HTTPException:
            session.rollback(); raise
        except ValueError as error:
            session.rollback(); raise HTTPException(409 if str(error).startswith('A saved PDF') else 422, str(error)) from None
        except Exception:
            session.rollback(); raise HTTPException(500, 'PDF upload could not be completed.') from None
        finally:
            try:
                if created and not keep: target.unlink(missing_ok=True)
            finally:
                PROCESSING_LOCK.release()

    @app.get("/api/jobs/{job_id}/claims")
    def claims(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        rows = session.scalars(select(ExtractedClaim).join(ModelRun, ExtractedClaim.model_run_id == ModelRun.id).join(ResearchJobPaper, ResearchJobPaper.paper_id == ExtractedClaim.paper_id).where(ModelRun.research_job_id == job_id, ModelRun.agent_role == AgentRole.CLAIM_EXTRACTION, ResearchJobPaper.research_job_id == job_id, ResearchJobPaper.analysis_selected.is_(True)).order_by(ExtractedClaim.created_at.desc()).limit(100)).all()
        def visible_review(claim):
            review=current_human_review(claim)
            if review is None: return None
            try:
                for link in claim.evidence_links: ensure_processed_source(link.evidence_chunk, link.evidence_chunk.source_document)
            except HTTPException: return None
            return review
        result=[]
        for c in rows:
            review=visible_review(c)
            result.append({"id": c.id, "statement": c.claim_text, "category": val(c.claim_type), "paper_id": c.paper_id, "paper_title": c.paper.title, "uncertainty": c.uncertainty_or_missing_information, "semantic_status": val(c.verification_result.semantic_status) if review else "unverified", "semantic_review": {"method":"human", "reviewer":review['reviewer'], "rationale":review['rationale'], "reviewed_at":review['reviewed_at']} if review else None, "review_locked": c.verification_result is not None, "evidence": [{"id": e.id, "chunk_id": e.evidence_chunk_id, "quotation": e.quotation_text, "page": e.evidence_chunk.page_number, "quotation_status": val(e.quotation_status), "location_status": val(e.source_location_status)} for e in c.evidence_links]})
        return result

    @app.post('/api/jobs/{job_id}/claims/{claim_id}/semantic-review', dependencies=[Depends(require_local_request)])
    def human_review(job_id: str, claim_id: str, body: HumanReviewRequest, session: Session = Depends(db)):
        job_or_404(session, job_id)
        claim=session.scalar(select(ExtractedClaim).join(ModelRun).where(ExtractedClaim.id==claim_id, ModelRun.research_job_id==job_id, ModelRun.agent_role==AgentRole.CLAIM_EXTRACTION))
        if claim is None or session.scalar(select(ResearchJobPaper.id).where(ResearchJobPaper.research_job_id==job_id,ResearchJobPaper.paper_id==claim.paper_id)) is None:
            raise HTTPException(404, 'Claim not found for this research job.')
        if not PROCESSING_LOCK.acquire(blocking=False): raise HTTPException(409, 'Another review or processing operation is in progress.')
        try:
            review=record_human_review(session,claim,body); session.commit()
            return {'status':review['status'],'method':'human','reviewer':review['reviewer'],'reviewed_at':review['reviewed_at']}
        except HTTPException:
            session.rollback(); raise
        except Exception:
            session.rollback(); raise HTTPException(409, 'Review could not be saved safely.') from None
        finally: PROCESSING_LOCK.release()

    @app.post("/api/jobs/{job_id}/claims/extract", dependencies=[Depends(require_local_request)])
    def extract_claims(job_id: str, body: ClaimExtractionRequest, session: Session = Depends(db)):
        if not body.cloud_consent:
            raise HTTPException(403, 'Explicit cloud-processing consent is required.')
        provider = InferenceProvider(body.provider)
        model = {InferenceProvider.GEMINI: 'gemini-3.5-flash-lite', InferenceProvider.GROQ: 'openai/gpt-oss-120b', InferenceProvider.OPENROUTER: 'inclusionai/ling-3.2-flash:free'}[provider]
        assignment = AgentModelAssignment(
            provider=provider,
            model_tag=model,
            output_tokens=512 if provider in {InferenceProvider.GEMINI, InferenceProvider.OPENROUTER} else 256,
            gemini_response_mode='plain_text' if provider is InferenceProvider.GEMINI else 'native_schema',
            prompt_template_version=PROMPT_VERSION,
        )
        if not PROCESSING_LOCK.acquire(blocking=False):
            raise HTTPException(409, 'Another document or extraction is being processed.')
        try:
            if claim_runner_factory:
                runner = claim_runner_factory(assignment)
            else:
                settings = load_cloud_inference_settings()
                budget = CloudBudget(max_requests=1, max_output_tokens_total=assignment.output_tokens)
                config = BlindspotModelConfig(claim_extraction=assignment)
                if provider is InferenceProvider.GEMINI:
                    runner = CloudInferenceRouter(config, settings, gemini=GeminiClient(api_key=settings.gemini_api_key, budget=budget))
                elif provider is InferenceProvider.GROQ:
                    runner = CloudInferenceRouter(config, settings, groq=GroqClient(api_key=settings.groq_api_key, budget=budget))
                else:
                    if not settings.openrouter_api_key:
                        raise ConfigurationError('OPENROUTER_API_KEY is required for OpenRouter inference.')
                    runner = CloudInferenceRouter(config, settings, openrouter=OpenRouterClient(api_key=settings.openrouter_api_key, budget=budget))
            result = run_development_extraction(session, job_id=job_id, chunk_id=body.chunk_id, development_chunk_ids=frozenset([body.chunk_id]), assignment=assignment, runner=runner, source_guard=ensure_processed_source, select_complete_spans=True)
            run = session.get(ModelRun, result.model_run_id)
            allowed_errors = {'CloudQuotaError', 'CloudTimeoutError', 'CloudInputLimitError', 'CloudInferenceError', 'CloudConfigurationError', 'invalid_request', 'unprocessable_request', 'provider_http_error', 'authentication_invalid', 'permission_denied', 'output_truncated', 'invalid_json', 'invalid_structure', 'missing_required_fields', 'invalid_field_value', 'empty_response', 'missing_response_content', 'provider_refusal', 'unexpected_transport_failure', 'unsupported_provider_response', 'ValidationRejections'}
            category = run.error_category if run and run.error_category in allowed_errors else ('inference_failed' if result.outcome == 'failed_inference' else None)
            http_status = run.model_config.get('http_diagnostics', {}).get('http_status') if run else None
            if type(http_status) is not int or not 100 <= http_status <= 599: http_status = None
            session.commit()
            return {'status': result.outcome, 'model_run_id': result.model_run_id, 'provider': provider.value, 'model': model, 'prompt_version': PROMPT_VERSION, 'accepted_count': result.accepted_count, 'rejected_count': result.rejected_count, 'rejection_codes': result.rejection_codes, 'reused': result.reused, 'error_category': category, 'http_status': http_status}
        except HTTPException:
            session.rollback(); raise
        except ConfigurationError:
            session.rollback(); raise HTTPException(503, 'Cloud configuration or free-tier confirmation is unavailable.') from None
        except NoAdmissiblePassage:
            session.rollback(); raise HTTPException(409, 'No complete interior evidence sentence fits the configured request budget.') from None
        except ValueError:
            session.rollback(); raise HTTPException(404, 'Job or eligible evidence chunk was not found.') from None
        except Exception:
            session.rollback(); raise HTTPException(503, 'Claim extraction could not be started.') from None
        finally:
            PROCESSING_LOCK.release()

    @app.get("/api/jobs/{job_id}/gaps")
    def gaps(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        rows = session.scalars(select(CandidateGap).where(CandidateGap.research_job_id == job_id).order_by(CandidateGap.created_at.desc()).limit(100)).all()
        automated = session.scalars(select(AutomatedProvisionalGap).where(AutomatedProvisionalGap.research_job_id == job_id).order_by(AutomatedProvisionalGap.created_at.desc()).limit(100)).all()
        return ([{"id": g.id, "statement": g.statement, "category": g.category, "explanation": g.explanation, "counterevidence": g.counterevidence, "uncertainty": g.uncertainty, "validation_status": g.validation_status, "references": [{"paper_id": r.paper_id, "claim_id": r.claim_id, "evidence_id": r.claim_evidence_id, "role": r.role} for r in g.references]} for g in rows] +
                [{"id": g.id, "statement": g.statement, "category": "automated_provisional", "explanation": g.rationale, "counterevidence": "Recorded in supporting references.", "uncertainty": g.uncertainty, "validation_status": "Provisional — Not globally validated.", "references": [{"paper_id": r.paper_id, "claim_id": r.claim_id, "evidence_id": r.claim_evidence_id, "role": r.role} for r in g.references]} for g in automated])

    @app.post("/api/jobs/{job_id}/gaps/detect", dependencies=[Depends(require_local_request)])
    def detect_gaps(job_id: str, body: GapDetectionRequest, session: Session = Depends(db)):
        if not body.cloud_consent:
            raise HTTPException(403, 'Explicit cloud-processing consent is required.')
        job_or_404(session, job_id)
        if not PROCESSING_LOCK.acquire(blocking=False):
            raise HTTPException(409, 'Another document or extraction is being processed.')
        try:
            heldout = heldout_document_ids()
            allowed = frozenset(session.scalars(select(EvidenceChunk.id).join(SourceDocument).join(ResearchJobPaper, ResearchJobPaper.paper_id == SourceDocument.paper_id).where(ResearchJobPaper.research_job_id == job_id, SourceDocument.id.not_in(heldout))).all())
            packet = prepare_packet(session, job_id, allowed_chunk_ids=allowed)
            if len({item['paper_id'] for item in packet if item['verification'] == 'supported'}) < 2:
                session.rollback()
                raise HTTPException(409, 'Gap detection requires current human-supported claims from at least two distinct papers with valid source evidence.')
            session.rollback()
            if gap_runner_factory is None:
                raise HTTPException(409, 'Gap detection provider execution is not enabled. Eligible evidence remains available for a later explicitly configured run.')
            assignment = BlindspotModelConfig().gap_detection
            result = run_gap_detection(session, job_id=job_id, allowed_chunk_ids=allowed, assignment=assignment, runner=gap_runner_factory(assignment))
            session.commit()
            return {'status': result.outcome, 'model_run_id': result.model_run_id, 'accepted_count': result.accepted_count, 'rejected_count': result.rejected_count, 'rejection_codes': result.rejection_codes, 'reused': result.reused}
        except HTTPException:
            raise
        except Exception:
            session.rollback()
            raise HTTPException(503, 'Gap detection could not be started; saved evidence is unchanged.') from None
        finally:
            PROCESSING_LOCK.release()

    @app.get("/api/jobs/{job_id}/report")
    def research_report(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        content = build_research_report(session, job_id)
        return PlainTextResponse(content, media_type='text/markdown; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="blindspot-{job_id}.md"'})

    @app.get("/api/jobs/{job_id}/report.pdf")
    def research_report_pdf(job_id: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        content = render_research_report_pdf(session, job_id)
        return Response(content, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="blindspot-{job_id}.pdf"'})

    @app.post("/api/jobs/{job_id}/agents/{agent}", dependencies=[Depends(require_local_request)])
    def unavailable_agent(job_id: str, agent: str, session: Session = Depends(db)):
        job_or_404(session, job_id)
        if agent not in {"claim-extraction", "gap-detection"}: raise HTTPException(404, "Agent not found.")
        raise HTTPException(409, "Agent execution is unavailable until live integration is verified.")

    return app


app = create_app()

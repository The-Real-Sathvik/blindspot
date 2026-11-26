from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.main import create_app
from app.models.research import EvidenceChunk, Paper, ResearchJob, ResearchJobPaper, SearchRun, SourceDocument
from app.schemas.research import DocumentStatus, JobStatus
from app.services.autonomous_research import AutonomousResearchOrchestrator
from app.services.model_config import AgentModelAssignment
from app.services.research_report import build_research_report


def test_autonomous_run_discovers_then_completes_honest_insufficient_evidence_report(session):
    job = ResearchJob(topic='Autonomous offline integration')
    session.add(job); session.commit()

    class Discovery:
        def discover(self, db, saved_job, **_kwargs):
            paper = Paper(title='Saved abstract metadata', authors=[], openalex_id='W-auto', abstract='Saved abstract only.', open_access_metadata={})
            db.add(ResearchJobPaper(research_job=saved_job, paper=paper, included=True, relevance_score=.9, decision_reason='Test shortlist'))
            return SimpleNamespace(retrieved_count=1, duplicate_count=0, shortlisted=[paper])

    result = AutonomousResearchOrchestrator(lambda: Discovery()).run(session, job)
    session.refresh(job)
    assert result.outcome == 'insufficient_evidence' and result.discovery == 'completed'
    assert result.abstract_available == 1 and result.evidence_ready == 0
    assert job.status is JobStatus.COMPLETED and 'insufficient full-text evidence' in job.error_info
    report = build_research_report(session, job.id)
    assert 'Saved abstract metadata' in report
    assert 'Preliminary abstract metadata' in report and 'do not enter the full-text claim or gap gates' in report
    assert 'No saved source-grounded claims are available.' in report
    assert 'No candidate gaps are available.' in report


def test_autonomous_run_reuses_saved_discovery_and_never_retries_permanent_document_failure(session):
    job = ResearchJob(topic='Restart checkpoint')
    paper = Paper(title='Existing shortlist record', authors=[], openalex_id='W-reuse', open_access_metadata={})
    session.add_all([job, ResearchJobPaper(research_job=job, paper=paper, included=True, relevance_score=.5)]); session.flush()
    session.add(SearchRun(research_job=job, source_name='openalex', query_text='saved', requested_limit=1)); session.commit()
    calls = []
    class Discovery:
        def discover(self, *_args, **_kwargs): calls.append(True); return SimpleNamespace()
    result = AutonomousResearchOrchestrator(lambda: Discovery()).run(session, job)
    assert calls == [] and result.discovery == 'reused' and result.outcome == 'insufficient_evidence'


def test_autonomous_endpoint_runs_offline_vertical_slice(tmp_path):
    engine = create_sqlite_engine('sqlite:///' + (tmp_path / 'autonomous.db').as_posix())
    Base.metadata.create_all(engine); factory = create_session_factory(engine)
    class OfflineRunner:
        def run(self, session, job):
            return AutonomousResearchOrchestrator(lambda: type('Discovery', (), {
                'discover': lambda _self, db, saved, **_kw: db.add(ResearchJobPaper(
                    research_job=saved, paper=Paper(title='Offline paper', authors=[], openalex_id='W-api', open_access_metadata={}),
                    included=True, relevance_score=.8)) or SimpleNamespace()
            })()).run(session, job)
    with TestClient(create_app(session_factory=factory, orchestrator_factory=lambda: OfflineRunner()), headers={'X-Blindspot-Request':'1'}) as client:
        job = client.post('/api/jobs', json={'topic':'Offline autonomous workflow'}).json()
        assert client.post(f"/api/jobs/{job['id']}/run", json={'cloud_consent': False}).status_code == 403
        response = client.post(f"/api/jobs/{job['id']}/run", json={'cloud_consent': True})
        assert response.status_code == 200 and response.json()['status'] == 'insufficient_evidence'
        details = client.get(f"/api/jobs/{job['id']}").json()
        assert details['paper_count'] == 1 and details['status'] == 'completed'
        assert 'insufficient full-text evidence' in details['warnings'][0]
        assert 'Offline paper' in client.get(f"/api/jobs/{job['id']}/report").text
    engine.dispose()


def test_autonomous_claim_handoff_is_capped_and_continues_after_a_failure(session, monkeypatch):
    """The orchestrator reuses V3 and makes at most one guarded request per three papers."""
    job = ResearchJob(topic='Bounded automated claims')
    session.add(job); session.flush()
    links = []
    for index in range(4):
        paper = Paper(title=f'Paper {index}', authors=[], openalex_id=f'W-{index}', open_access_metadata={})
        document = SourceDocument(paper=paper, document_type='pdf', status=DocumentStatus.AVAILABLE, local_path='fixture.pdf')
        chunk = EvidenceChunk(source_document=document, page_number=1, chunk_index=0, start_offset=0, end_offset=24, text='A complete fixture finding.')
        links.append(ResearchJobPaper(research_job=job, paper=paper, included=True, relevance_score=1 - index / 10))
        session.add_all([links[-1], chunk])
    session.flush(); session.add(SearchRun(research_job=job, source_name='fixture', query_text='fixture', requested_limit=4)); session.commit()
    calls = []
    monkeypatch.setattr('app.services.autonomous_research.job_document_state', lambda *_args: {'status': 'extracted'})
    def fake_v3(_session, *, chunk_id, **_kwargs):
        calls.append(chunk_id)
        outcome = 'failed_inference' if len(calls) == 2 else 'claims_extracted'
        return SimpleNamespace(model_run_id='missing-run', outcome=outcome, accepted_count=1 if outcome == 'claims_extracted' else 0)
    monkeypatch.setattr('app.services.autonomous_research.run_development_extraction', fake_v3)
    assignment = AgentModelAssignment(provider='gemini', model_tag='gemini-3.5-flash-lite', output_tokens=512,
        gemini_response_mode='plain_text', prompt_template_version='claim-extraction-v3')
    result = AutonomousResearchOrchestrator(lambda: None, claim_runner=object(), claim_assignment=assignment, max_claim_papers=3).run(session, job)
    assert len(calls) == 3
    assert result.claim_attempted == 3 and result.claim_accepted == 2 and result.claim_failed == 1
    session.refresh(links[0]); session.refresh(links[1]); session.refresh(links[2]); session.refresh(links[3])
    assert links[0].error_info.startswith('[claim-extraction] completed')
    assert links[1].error_info.startswith('[claim-extraction] failed_inference')
    assert links[2].error_info.startswith('[claim-extraction] completed')
    assert links[3].error_info is None


def test_autonomous_claim_handoff_uses_only_saved_extracted_alternative_when_shortlist_has_no_evidence(session, monkeypatch):
    job = ResearchJob(topic='Alternative evidence fallback')
    shortlisted = Paper(title='Shortlist without PDF', authors=[], openalex_id='W-short', open_access_metadata={})
    alternative = Paper(title='Saved verified alternative', authors=[], openalex_id='W-alt', open_access_metadata={})
    document = SourceDocument(paper=alternative, document_type='pdf', status=DocumentStatus.AVAILABLE, local_path='fixture.pdf')
    chunk = EvidenceChunk(source_document=document, page_number=1, chunk_index=0, start_offset=0, end_offset=24, text='A complete fixture finding.')
    session.add_all([
        job,
        ResearchJobPaper(research_job=job, paper=shortlisted, included=True, relevance_score=1.0),
        ResearchJobPaper(research_job=job, paper=alternative, included=False, relevance_score=.8),
        chunk,
    ])
    session.flush(); session.add(SearchRun(research_job=job, source_name='fixture', query_text='fixture', requested_limit=2)); session.commit()
    monkeypatch.setattr('app.services.autonomous_research.job_document_state', lambda _s, _j, paper: {'status': 'extracted'} if paper.id == alternative.id else {'status': 'unavailable'})
    calls = []
    monkeypatch.setattr('app.services.autonomous_research.run_development_extraction', lambda _s, *, chunk_id, **_kw: calls.append(chunk_id) or SimpleNamespace(model_run_id='missing-run', outcome='no_claims', accepted_count=0))
    assignment = AgentModelAssignment(provider='gemini', model_tag='gemini-3.5-flash-lite', output_tokens=512,
        gemini_response_mode='plain_text', prompt_template_version='claim-extraction-v3')
    result = AutonomousResearchOrchestrator(lambda: None, claim_runner=object(), claim_assignment=assignment).run(session, job)
    assert calls == [chunk.id]
    assert result.claim_attempted == 1 and result.claim_abstained == 1
    saved_link = session.query(ResearchJobPaper).filter_by(research_job_id=job.id, paper_id=alternative.id).one()
    assert saved_link.included is False and saved_link.error_info.startswith('[claim-extraction] abstained')


def test_autonomous_recovery_processes_eligible_alternatives_without_changing_shortlist(session, monkeypatch):
    """A source-poor shortlist falls through to bounded, job-owned OA alternatives."""
    job = ResearchJob(topic='Source-aware recovery')
    shortlist = Paper(title='Relevant but no permitted source', authors=[], openalex_id='W-short', open_access_metadata={})
    failed = Paper(title='First permitted alternative', authors=[], openalex_id='W-first', open_access_metadata={})
    recovered = Paper(title='Second permitted alternative', authors=[], openalex_id='W-second', open_access_metadata={})
    links = [
        ResearchJobPaper(research_job=job, paper=shortlist, included=True, relevance_score=1.0),
        ResearchJobPaper(research_job=job, paper=failed, included=False, relevance_score=.9),
        ResearchJobPaper(research_job=job, paper=recovered, included=False, relevance_score=.8),
    ]
    session.add_all([job, *links]); session.flush()
    session.add(SearchRun(research_job=job, source_name='fixture', query_text='fixture', requested_limit=3)); session.commit()
    states = {
        shortlist.id: {'status': 'unavailable', 'can_acquire': False, 'can_extract': False},
        failed.id: {'status': 'available_for_acquisition', 'can_acquire': True, 'can_extract': False},
        recovered.id: {'status': 'available_for_acquisition', 'can_acquire': True, 'can_extract': False},
    }
    calls = []
    monkeypatch.setattr('app.services.autonomous_research.job_document_state', lambda _s, _j, paper: states[paper.id])
    def attempt(_session, _job_id, paper, operation, **_kwargs):
        calls.append((paper.id, operation))
        if paper.id == failed.id:
            states[paper.id] = {'status': 'failed', 'can_acquire': False, 'can_extract': False,
                                'attempt': {'outcome': 'failed', 'retryable': False}}
        elif operation == 'acquire':
            states[paper.id] = {'status': 'downloaded', 'can_acquire': False, 'can_extract': True}
        else:
            states[paper.id] = {'status': 'extracted', 'can_acquire': False, 'can_extract': False}
        return states[paper.id]
    monkeypatch.setattr('app.services.autonomous_research.perform_attempt', attempt)

    result = AutonomousResearchOrchestrator(lambda: None, max_recovery_papers=2).run(session, job)

    assert calls == [(failed.id, 'acquire'), (recovered.id, 'acquire'), (recovered.id, 'extract')]
    assert result.evidence_ready == 1 and result.failed_documents == 1
    assert links[0].included is True and links[1].included is False and links[2].included is False


def test_autonomous_recovery_respects_candidate_budget_and_skips_prior_failed_attempts(session, monkeypatch):
    job = ResearchJob(topic='Bounded source recovery')
    shortlist = Paper(title='No source', authors=[], openalex_id='W-short', open_access_metadata={})
    skipped = Paper(title='Previous failed source', authors=[], openalex_id='W-skip', open_access_metadata={})
    eligible = Paper(title='Eligible but outside budget', authors=[], openalex_id='W-eligible', open_access_metadata={})
    session.add_all([
        job,
        ResearchJobPaper(research_job=job, paper=shortlist, included=True, relevance_score=1),
        ResearchJobPaper(research_job=job, paper=skipped, included=False, relevance_score=.9),
        ResearchJobPaper(research_job=job, paper=eligible, included=False, relevance_score=.8),
    ]); session.flush(); session.add(SearchRun(research_job=job, source_name='fixture', query_text='fixture', requested_limit=3)); session.commit()
    states = {
        shortlist.id: {'status':'unavailable', 'can_acquire':False, 'can_extract':False},
        skipped.id: {'status':'failed', 'can_acquire':True, 'can_extract':False, 'attempt':{'outcome':'failed', 'retryable':None}},
        eligible.id: {'status':'available_for_acquisition', 'can_acquire':True, 'can_extract':False},
    }
    calls = []
    monkeypatch.setattr('app.services.autonomous_research.job_document_state', lambda _s, _j, paper: states[paper.id])
    monkeypatch.setattr('app.services.autonomous_research.perform_attempt', lambda *_args, **_kwargs: calls.append(True))
    result = AutonomousResearchOrchestrator(lambda: None, max_recovery_papers=1).run(session, job)
    assert calls == [] and result.evidence_ready == 0 and result.failed_documents == 1

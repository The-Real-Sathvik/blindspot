from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.main import create_app
from app.models.research import Paper, ResearchJob, ResearchJobPaper, SearchRun
from app.schemas.research import JobStatus
from app.services.autonomous_research import AutonomousResearchOrchestrator
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
        response = client.post(f"/api/jobs/{job['id']}/run")
        assert response.status_code == 200 and response.json()['status'] == 'insufficient_evidence'
        details = client.get(f"/api/jobs/{job['id']}").json()
        assert details['paper_count'] == 1 and details['status'] == 'completed'
        assert 'insufficient full-text evidence' in details['warnings'][0]
        assert 'Offline paper' in client.get(f"/api/jobs/{job['id']}/report").text
    engine.dispose()

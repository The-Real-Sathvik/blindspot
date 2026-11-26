from fastapi.testclient import TestClient
import pytest
from types import SimpleNamespace

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.main import create_app
from app.models.research import CandidateGap, CandidateGapReference, ClaimEvidence, EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJobPaper, SourceDocument
from app.schemas.research import AgentRole, CitationCheckStatus, ClaimType, ModelRunStatus


@pytest.fixture
def web(tmp_path):
    engine = create_sqlite_engine("sqlite:///" + (tmp_path / "web.db").as_posix())
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with TestClient(create_app(session_factory=factory), headers={'X-Blindspot-Request':'1'}) as client:
        yield client, factory
    engine.dispose()


def test_create_list_empty_states_and_unavailable_agents(web):
    client, _ = web
    assert client.get('/api/jobs').json() == []
    assert client.post('/api/jobs', json={'topic': '  '}).status_code == 422
    response = client.post('/api/jobs', json={'topic': '  Traceable detection methods  '})
    assert response.status_code == 201
    job = response.json(); assert job['topic'] == 'Traceable detection methods'
    assert client.get('/api/jobs').json()[0]['id'] == job['id']
    for section in ('papers', 'evidence', 'claims', 'gaps'):
        assert client.get(f"/api/jobs/{job['id']}/{section}").json() == []
    details = client.get(f"/api/jobs/{job['id']}").json()
    assert any(s['state'] == 'blocked' for s in details['stages'])
    assert client.post(f"/api/jobs/{job['id']}/agents/claim-extraction").status_code == 409
    assert client.get('/api/jobs/missing').status_code == 404
    assert client.post('/api/jobs', json={'topic': 'Cross-site request'}, headers={'X-Blindspot-Request': ''}).status_code == 403


def test_real_paper_and_evidence_serialization(web):
    client, factory = web
    job_id = client.post('/api/jobs', json={'topic': 'Test scholarly evidence'}).json()['id']
    with factory() as session:
        paper = Paper(title='Real saved paper', authors=[{'name':'Researcher'}], openalex_id='W1', publication_year=2020, landing_page_url='javascript:alert(1)', open_access_metadata={})
        doc = SourceDocument(paper=paper, document_type='pdf')
        chunk = EvidenceChunk(source_document=doc, page_number=2, chunk_index=0, start_offset=0, end_offset=12, text='Exact source')
        session.add_all([ResearchJobPaper(research_job_id=job_id, paper=paper, included=True, relevance_score=0.8, decision_reason='Topic match'), chunk]); session.commit()
    p = client.get(f'/api/jobs/{job_id}/papers').json()[0]
    assert p['title'] == 'Real saved paper' and p['included'] and p['landing_page_url'] is None
    evidence = client.get(f'/api/jobs/{job_id}/evidence').json()[0]
    assert evidence['chunks'][0]['text'] == 'Exact source' and evidence['chunks'][0]['page'] == 2


def test_discovery_failure_does_not_expose_secrets_or_partial_results(web):
    client, factory = web
    job_id = client.post('/api/jobs', json={'topic':'Test discovery failures'}).json()['id']
    class Failing:
        def discover(self, session, job, **kwargs):
            session.add(Paper(title='Should be rolled back', authors=[], open_access_metadata={}))
            raise RuntimeError('private-api-key')
    app = create_app(session_factory=factory, discovery_factory=lambda: Failing())
    with TestClient(app, headers={'X-Blindspot-Request':'1'}) as test_client:
        response = test_client.post(f'/api/jobs/{job_id}/discover')
        assert response.status_code == 502 and 'private-api-key' not in response.text
        assert test_client.get(f'/api/jobs/{job_id}/papers').json() == []


def test_explicit_discovery_persists_results(web):
    client, factory = web
    job_id = client.post('/api/jobs', json={'topic':'Discover saved papers'}).json()['id']
    class FakeDiscovery:
        def discover(self, session, job, **kwargs):
            paper = Paper(title='Mock discovery record', authors=[], open_access_metadata={})
            session.add(ResearchJobPaper(research_job=job, paper=paper, included=True, decision_reason='Selected by test'))
            return SimpleNamespace(retrieved_count=1, duplicate_count=0, shortlisted=[paper])
    with TestClient(create_app(session_factory=factory, discovery_factory=lambda: FakeDiscovery()), headers={'X-Blindspot-Request':'1'}) as test_client:
        assert test_client.post(f'/api/jobs/{job_id}/discover').json()['shortlisted'] == 1
        assert test_client.get(f'/api/jobs/{job_id}/papers').json()[0]['title'] == 'Mock discovery record'


def test_claim_and_gap_render_with_separate_verification_and_no_secrets(web):
    client, factory = web
    job_id = client.post('/api/jobs', json={'topic':'Review evidence and gaps'}).json()['id']
    with factory() as session:
        paper = Paper(title='Source paper', authors=[], open_access_metadata={})
        doc = SourceDocument(paper=paper, document_type='pdf')
        chunk = EvidenceChunk(source_document=doc, page_number=4, chunk_index=0, start_offset=0, end_offset=6, text='Source')
        run = ModelRun(research_job_id=job_id, agent_role=AgentRole.CLAIM_EXTRACTION, model_tag='test', model_config={'private_key':'never-expose-this'}, prompt_template_version='test', status=ModelRunStatus.SUCCESS)
        claim = ExtractedClaim(paper=paper, model_run=run, claim_type=ClaimType.RESULT, claim_text='A claim')
        evidence = ClaimEvidence(claim=claim, evidence_chunk=chunk, quotation_text='Source', quote_start_offset=0, quote_end_offset=6, quotation_status=CitationCheckStatus.VALID, source_location_status=CitationCheckStatus.VALID)
        session.add_all([ResearchJobPaper(research_job_id=job_id, paper=paper), evidence]); session.flush()
        gap = CandidateGap(research_job_id=job_id, model_run_id=run.id, statement='A possible gap', category='missing_evaluation', explanation='Limited records', counterevidence='Unknown', uncertainty='Not reviewed', validation_status='not_yet_validated')
        session.add(CandidateGapReference(gap=gap, paper_id=paper.id, claim_id=claim.id, claim_evidence_id=evidence.id, role='supporting')); session.commit()
    response = client.get(f'/api/jobs/{job_id}/claims')
    item = response.json()[0]
    assert item['semantic_status'] == 'unverified' and item['evidence'][0]['quotation_status'] == 'valid' and item['evidence'][0]['page'] == 4
    assert 'never-expose-this' not in response.text
    gap = client.get(f'/api/jobs/{job_id}/gaps').json()[0]
    assert gap['validation_status'] == 'not_yet_validated' and gap['references'][0]['role'] == 'supporting'


def test_partial_report_is_downloadable_and_never_invents_claims(web):
    client, _ = web
    job_id = client.post('/api/jobs', json={'topic':'Honest partial report'}).json()['id']
    response = client.get(f'/api/jobs/{job_id}/report')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/markdown')
    assert 'Honest partial report' in response.text
    assert 'No saved source-grounded claims are available.' in response.text
    assert 'No candidate gaps are available.' in response.text

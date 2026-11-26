import json
from hashlib import sha256

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.main import create_app
from app.models.research import EvidenceChunk, ExtractedClaim, ModelRun, Paper, ResearchJobPaper, SourceDocument
from app.schemas.research import DocumentStatus
from app.core.config import CloudInferenceSettings
from app.services.cloud_inference import CloudInferenceError, CloudQuotaError, GeminiClient
from app.services.fulltext import _normalise_text
from app.services.ollama_client import InferenceMetrics


class MockRunner:
    def __init__(self, mode='valid'): self.mode, self.calls = mode, 0
    def generate(self, role, prompt, schema):
        self.calls += 1
        if self.mode == 'quota': raise CloudQuotaError('secret provider detail')
        if self.mode == 'unavailable': raise CloudInferenceError('secret transport detail')
        spans = json.loads(prompt.rsplit('\n', 1)[1])
        if self.mode == 'abstain': return schema(claims=[]), InferenceMetrics(0.1, 2, None)
        identity = 'ev_invented' if self.mode == 'invalid' else spans[0]['evidence_id']
        claim = {'claim_statement':'The fixture reports 42 participants.', 'claim_category':'dataset', 'evidence_ids':[identity], 'uncertainty_or_missing_information':'Synthetic test.'}
        return schema(claims=[claim]), InferenceMetrics(0.1, 10, None)


@pytest.fixture
def web_claims(tmp_path, monkeypatch):
    import app.services.web_fulltext as fulltext
    monkeypatch.setattr(fulltext, 'SOURCE_DIR', tmp_path)
    engine = create_sqlite_engine('sqlite:///' + (tmp_path / 'claims.db').as_posix()); Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    runner = MockRunner()
    app = create_app(session_factory=factory, claim_runner_factory=lambda assignment: runner)
    client = TestClient(app, headers={'X-Blindspot-Request':'1'})
    job = client.post('/api/jobs', json={'topic':'Synthetic claim extraction'}).json()['id']
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72,72), 'Beginning text. The fixture reports 42 participants. Ending text.'); data = pdf.tobytes(); pdf.close()
    path = tmp_path / 'source.pdf'; path.write_bytes(data)
    with fitz.open(path) as doc: source = _normalise_text(doc[0].get_text('text'))
    with factory() as session:
        paper = Paper(title='Synthetic paper', authors=[], open_access_metadata={})
        document = SourceDocument(paper=paper, document_type='pdf', status=DocumentStatus.AVAILABLE, local_path=str(path), content_hash=sha256(data).hexdigest())
        chunk = EvidenceChunk(source_document=document, page_number=1, chunk_index=0, start_offset=0, end_offset=len(source), text=source)
        session.add_all([ResearchJobPaper(research_job_id=job, paper=paper), chunk]); session.commit()
        chunk_id, doc_id = chunk.id, document.id
    yield client, factory, runner, job, chunk_id, doc_id, path
    client.close(); engine.dispose()


def invoke(client, job, chunk, consent=True, provider='gemini'):
    return client.post(f'/api/jobs/{job}/claims/extract', json={'chunk_id':chunk, 'provider':provider, 'cloud_consent':consent})


def test_unreviewed_upload_cannot_enter_cloud_claim_extraction(web_claims):
    client, factory, runner, job, chunk, document_id, _ = web_claims
    with factory() as session:
        session.get(SourceDocument, document_id).source_url = 'user-upload:unverified'
        session.commit()
    assert client.get(f'/api/jobs/{job}/evidence').json()[0]['claims_available'] is False
    response = invoke(client, job, chunk)
    assert response.status_code == 409 and runner.calls == 0


def test_synthetic_explicit_human_review_is_separate_and_bound_to_evidence(web_claims):
    from app.models.research import ExtractedClaim
    client, factory, _, job, chunk, _, _ = web_claims
    assert invoke(client,job,chunk).json()['accepted_count']==1
    claim=client.get(f'/api/jobs/{job}/claims').json()[0]
    assert claim['semantic_status']=='unverified' and claim['semantic_review'] is None
    url=f"/api/jobs/{job}/claims/{claim['id']}/semantic-review"
    payload={'semantic_status':'supported','reviewer':'SYNTHETIC TEST ONLY','rationale':'Synthetic fixture assessment, not a human review.', 'reviewed_evidence':False}
    assert client.post(url,json=payload).status_code==403
    payload['reviewed_evidence']=True
    assert client.post(url,json=payload).json()['status']=='supported'
    reviewed=client.get(f'/api/jobs/{job}/claims').json()[0]
    assert reviewed['semantic_status']=='supported' and reviewed['semantic_review']['reviewer']=='SYNTHETIC TEST ONLY'
    assert client.post(url,json=payload).status_code==409
    with factory() as session:
        session.get(ExtractedClaim,claim['id']).claim_text='Changed after review.';session.commit()
    stale=client.get(f'/api/jobs/{job}/claims').json()[0]
    assert stale['semantic_status']=='unverified' and stale['review_locked'] is True


def test_legacy_or_invalid_review_is_never_promoted(web_claims):
    from app.models.research import ExtractedClaim, VerificationResult
    from app.schemas.research import SemanticSupportStatus
    client, factory, _, job, chunk, _, _ = web_claims
    invoke(client,job,chunk)
    claim=client.get(f'/api/jobs/{job}/claims').json()[0]
    with factory() as session:
        row=session.get(ExtractedClaim,claim['id'])
        session.add(VerificationResult(claim=row,semantic_status=SemanticSupportStatus.SUPPORTED,rationale='No provenance',verifier_version=None));session.commit()
    assert client.get(f'/api/jobs/{job}/claims').json()[0]['semantic_status']=='unverified'


def test_human_review_rejects_wrong_job_and_broken_citation(web_claims):
    from app.models.research import ExtractedClaim
    client,factory,_,job,chunk,_,_=web_claims
    invoke(client,job,chunk)
    claim=client.get(f'/api/jobs/{job}/claims').json()[0]
    body={'semantic_status':'supported','reviewer':'SYNTHETIC TEST ONLY','rationale':'Synthetic fixture.','reviewed_evidence':True}
    other=client.post('/api/jobs',json={'topic':'Other fixture'}).json()['id']
    assert client.post(f"/api/jobs/{other}/claims/{claim['id']}/semantic-review",json=body).status_code==404
    with factory() as session:
        session.get(ExtractedClaim,claim['id']).evidence_links[0].quotation_text='Fabricated quotation';session.commit()
    assert client.post(f"/api/jobs/{job}/claims/{claim['id']}/semantic-review",json=body).status_code==409
    assert client.get(f'/api/jobs/{job}/claims').json()[0]['semantic_status']=='unverified'


def test_consent_valid_claim_and_idempotency(web_claims, monkeypatch):
    client, factory, runner, job, chunk, doc, _ = web_claims
    monkeypatch.setattr('app.services.cloud_inference.urlopen', lambda *_a, **_k: pytest.fail('unexpected network'))
    assert invoke(client, job, chunk, False).status_code == 403
    assert client.post(f'/api/jobs/{job}/claims/extract', json={'chunk_id':chunk}).status_code == 422
    assert runner.calls == 0
    assert client.get(f'/api/jobs/{job}/evidence').json()[0]['claims_available'] is True
    result = invoke(client, job, chunk).json()
    assert result['status'] == 'claims_extracted' and result['accepted_count'] == 1
    claim = client.get(f'/api/jobs/{job}/claims').json()[0]
    assert claim['semantic_status'] == 'unverified' and claim['evidence'][0]['quotation_status'] == 'valid'
    assert claim['evidence'][0]['page'] == 1 and '42 participants' in claim['evidence'][0]['quotation']
    second = invoke(client, job, chunk).json()
    assert second['reused'] and second['model_run_id'] == result['model_run_id'] and runner.calls == 1
    with factory() as session:
        assert len(session.scalars(select(ExtractedClaim)).all()) == 1
        run = session.get(ModelRun, result['model_run_id'])
        assert run.model_config['provider'] == 'gemini' and run.prompt_template_version == 'claim-extraction-v3'
        assert run.model_config['gemini_response_mode'] == 'plain_text' and run.model_config['output_tokens'] == 512


def test_gemini_v3_route_uses_prompt_json_512_and_records_assignment(web_claims, monkeypatch):
    """The live route must pass its recorded Gemini assignment into the adapter unchanged."""
    client, factory, _, job, chunk, _, _ = web_claims
    captured = []

    def send(payload, _headers):
        captured.append(payload)
        evidence_id = json.loads(payload['contents'][0]['parts'][0]['text'].rsplit('\n', 1)[1])[0]['evidence_id']
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': f'CLAIM: The fixture reports 42 participants.\nEVIDENCE_ID: {evidence_id}'}
        ]}}]}

    monkeypatch.setattr('app.main.load_cloud_inference_settings', lambda: CloudInferenceSettings(
        gemini_api_key='fixture-key', groq_api_key='fixture-key', openrouter_api_key=None,
    ))
    monkeypatch.setattr('app.main.GeminiClient', lambda **kwargs: GeminiClient(request_fn=send, **kwargs))
    # Rebuild without the fixture's injected runner so the real route-to-adapter path is exercised.
    app = create_app(session_factory=factory)
    live_route_client = TestClient(app, headers={'X-Blindspot-Request': '1'})
    try:
        response = invoke(live_route_client, job, chunk)
    finally:
        live_route_client.close()

    assert response.status_code == 200 and response.json()['status'] == 'claims_extracted'
    assert len(captured) == 1
    config = captured[0]['generationConfig']
    assert config['maxOutputTokens'] == 512
    assert config['thinkingConfig'] == {'thinkingLevel': 'MINIMAL'}
    assert 'responseMimeType' not in config and 'responseJsonSchema' not in config
    assert 'CLAIM: <one concise factual claim supported by the supplied evidence>' in captured[0]['contents'][0]['parts'][0]['text']
    with factory() as session:
        run = session.get(ModelRun, response.json()['model_run_id'])
        assert run.model_tag == 'gemini-3.5-flash-lite'
        assert run.model_config['gemini_response_mode'] == 'plain_text'
        assert run.model_config['output_tokens'] == 512


@pytest.mark.parametrize('mode,status,accepted,rejected', [('invalid','partial_error',0,1), ('abstain','no_claims',0,0), ('quota','failed_inference',0,0), ('unavailable','failed_inference',0,0)])
def test_failure_abstention_and_safe_errors(web_claims, mode, status, accepted, rejected):
    client, factory, runner, job, chunk, _, _ = web_claims; runner.mode = mode
    response = invoke(client, job, chunk, provider='groq')
    assert response.status_code == 200 and response.json()['status'] == status
    assert (response.json()['accepted_count'], response.json()['rejected_count']) == (accepted, rejected)
    assert runner.calls == 1 and 'secret' not in response.text
    if mode == 'invalid': assert response.json()['rejection_codes'] == ['unknown_evidence_id']
    if mode == 'quota': assert response.json()['error_category'] == 'CloudQuotaError'
    with factory() as session:
        run = session.get(ModelRun, response.json()['model_run_id'])
        assert run.model_config['provider'] == 'groq' and 'secret' not in (run.error_summary or '')


def test_openrouter_selection_requires_consent_and_preserves_provenance(web_claims):
    client, factory, runner, job, chunk, _, _ = web_claims
    assert invoke(client, job, chunk, False, provider='openrouter').status_code == 403
    response = invoke(client, job, chunk, provider='openrouter')
    assert response.status_code == 200 and response.json()['provider'] == 'openrouter'
    with factory() as session:
        run = session.get(ModelRun, response.json()['model_run_id'])
        assert run.model_tag == 'inclusionai/ling-3.2-flash:free'
        assert run.model_config['provider'] == 'openrouter' and run.model_config['output_tokens'] == 512
    assert runner.calls == 1


def test_openrouter_rejects_caller_model_override_before_provider_execution(web_claims):
    client, _, runner, job, chunk, _, _ = web_claims
    response = client.post(f'/api/jobs/{job}/claims/extract', json={
        'chunk_id': chunk, 'provider': 'openrouter', 'cloud_consent': True,
        'model': 'baidu/ernie-5.5-flash-preview:free',
    })
    assert response.status_code == 422 and runner.calls == 0


def test_wrong_job_heldout_and_pdf_mismatch_fail_closed(web_claims, monkeypatch):
    client, _, runner, job, chunk, document_id, path = web_claims
    other = client.post('/api/jobs', json={'topic':'Different job'}).json()['id']
    assert invoke(client, other, chunk).status_code == 404
    monkeypatch.setattr('app.services.web_claims.heldout_document_ids', lambda: frozenset([document_id]))
    assert invoke(client, job, chunk).status_code == 403
    monkeypatch.setattr('app.services.web_claims.heldout_document_ids', lambda: frozenset())
    path.write_bytes(b'%PDF-changed')
    assert invoke(client, job, chunk).status_code == 409
    assert runner.calls == 0


def test_missing_partition_and_bad_provider_are_blocked(web_claims, monkeypatch, tmp_path):
    client, _, runner, job, chunk, _, _ = web_claims
    monkeypatch.setattr('app.services.web_claims.HELDOUT_MANIFEST', tmp_path / 'missing.json')
    assert invoke(client, job, chunk).status_code == 503
    assert invoke(client, job, chunk, provider='ollama').status_code == 422
    assert runner.calls == 0

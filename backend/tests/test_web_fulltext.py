from io import BytesIO
from urllib.error import URLError
from urllib.error import HTTPError
import socket
import ssl

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import create_session_factory, create_sqlite_engine
from app.main import create_app
from app.models.research import Paper, ResearchJobPaper
from app.services.fulltext import PdfDownloader, UnsafeUrlError
from app.services.web_fulltext import DeadlineResponse, PROCESSING_LOCK, PublicHTTPSConnection, PublicHTTPSHandler, RestrictedRedirect, approved_pdf_url
from app.services.document_attempts import _log_development_exception


class FakeResponse:
    def __init__(self, data):
        self.file = BytesIO(data); self.headers = {'Content-Type': 'application/pdf', 'Content-Length': str(len(data))}
    def read(self, amount=-1): return self.file.read(amount)
    def geturl(self): return 'https://arxiv.org/pdf/1234.5678'
    def __enter__(self): return self
    def __exit__(self, *_args): self.file.close()


def sample_pdf():
    pdf = fitz.open()
    for value in ['First page evidence.', 'Second page evidence.']:
        page = pdf.new_page(); page.insert_text((72, 72), value)
    result = pdf.tobytes(); pdf.close(); return result


@pytest.fixture
def setup_web(tmp_path, monkeypatch):
    import app.services.web_fulltext as module
    monkeypatch.setattr(module, 'SOURCE_DIR', tmp_path / 'pdfs')
    engine = create_sqlite_engine('sqlite:///' + (tmp_path / 'test.db').as_posix()); Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    def create(data, *, limit=20 * 1024 * 1024):
        count = []
        def opener(*_args, **_kwargs):
            count.append(1)
            if data is None: raise URLError('private network detail')
            return FakeResponse(data)
        app = create_app(session_factory=factory, fulltext_downloader_factory=lambda: PdfDownloader(opener=opener, max_bytes=limit))
        client = TestClient(app, headers={'X-Blindspot-Request':'1'})
        job_id = client.post('/api/jobs', json={'topic':'PDF workflow test'}).json()['id']
        with factory() as session:
            paper = Paper(title='PDF fixture', authors=[], open_access_metadata={'oa_url':'https://arxiv.org/pdf/1234.5678'})
            session.add(ResearchJobPaper(research_job_id=job_id, paper=paper)); session.commit(); paper_id = paper.id
        return client, job_id, paper_id, count
    yield create, factory
    engine.dispose()


def test_end_to_end_acquire_extract_and_repeat(setup_web, monkeypatch):
    create, _ = setup_web
    monkeypatch.setattr('app.services.cloud_inference.CloudInferenceRouter.generate', lambda *_args, **_kwargs: pytest.fail('AI inference must not run'))
    client, job, paper, calls = create(sample_pdf())
    base = f'/api/jobs/{job}/papers/{paper}'
    assert client.get(base + '/document').json()['status'] == 'available_for_acquisition'
    assert client.post(base + '/acquire').json()['status'] == 'downloaded'
    assert client.post(base + '/acquire').json()['status'] == 'downloaded' and len(calls) == 1
    result = client.post(base + '/extract').json()
    assert result['status'] == 'extracted' and result['chunk_count'] == 2
    evidence = client.get(f'/api/jobs/{job}/evidence').json()[0]['chunks']
    assert [c['page'] for c in evidence] == [1, 2]
    assert 'First page evidence.' in evidence[0]['text'] and evidence[0]['start_offset'] == 0
    assert client.post(base + '/extract').json()['chunk_count'] == 2
    assert len(client.get(f'/api/jobs/{job}/evidence').json()[0]['chunks']) == 2
    assert client.get(f'/api/jobs/{job}/papers').json()[0]['document']['status'] == 'extracted'
    assert client.get(f'/api/jobs/{job}/papers/not-associated/document').status_code == 404


@pytest.mark.parametrize('data,limit,code,retryable', [(None, 20000, 'network_error', None), (b'not-pdf', 20000, 'invalid_pdf', False), (b'%PDF-' + b'x' * 100, 10, 'pdf_too_large', False)])
def test_download_failures_are_safe_and_retriable(setup_web, data, limit, code, retryable):
    create, _ = setup_web; client, job, paper, calls = create(data, limit=limit)
    result = client.post(f'/api/jobs/{job}/papers/{paper}/acquire')
    assert result.status_code == 200 and result.json()['status'] == 'failed'
    assert result.json()['attempt']['code'] == code and result.json()['attempt']['retryable'] is retryable
    assert 'private network detail' not in result.text and len(calls) == 1


def test_opt_in_internal_attempt_traceback_is_sanitized(monkeypatch, caplog):
    monkeypatch.setenv('BLINDSPOT_LOG_DOCUMENT_EXCEPTIONS', 'true')
    sensitive = 'private-download-detail'
    try:
        raise RuntimeError(sensitive)
    except RuntimeError as error:
        _log_development_exception(error, operation='acquire', paper_id='paper-fixture')
    assert 'paper-fixture' in caplog.text and 'RuntimeError' in caplog.text
    assert sensitive not in caplog.text


def test_malformed_pdf_extraction_fails_without_evidence(setup_web):
    create, _ = setup_web; client, job, paper, calls = create(b'%PDF-not-a-valid-document')
    base = f'/api/jobs/{job}/papers/{paper}'
    result = client.post(base + '/acquire').json()
    assert result['status'] == 'failed' and result['attempt']['code'] == 'invalid_pdf'
    assert client.get(f'/api/jobs/{job}/evidence').json() == []


def test_failed_extraction_can_retry_without_redownloading(setup_web, monkeypatch):
    from app.services.fulltext import ExtractionError, FullTextService
    create, _ = setup_web; client, job, paper, calls = create(sample_pdf())
    base = f'/api/jobs/{job}/papers/{paper}'
    assert client.post(base + '/acquire').json()['status'] == 'downloaded'
    original = FullTextService._extract
    attempts = []
    def flaky(service, document):
        attempts.append(1)
        if len(attempts) == 1: raise ExtractionError('temporary')
        return original(service, document)
    monkeypatch.setattr(FullTextService, '_extract', flaky)
    assert client.post(base + '/extract').json()['status'] == 'failed'
    assert client.post(base + '/acquire').json()['status'] == 'failed' and len(calls) == 1
    assert client.post(base + '/extract').json()['status'] == 'extracted'
    assert len(calls) == 1 and len(attempts) == 2


def test_unsupported_source_and_prevalidated_redirect(setup_web):
    create, factory = setup_web; client, job, paper_id, calls = create(sample_pdf())
    with factory() as session:
        paper = session.get(Paper, paper_id); paper.open_access_metadata = {'oa_url':'https://publisher.example/paper.pdf'}; session.commit()
    status = client.post(f'/api/jobs/{job}/papers/{paper_id}/acquire').json()
    assert status['status'] == 'failed' and status['attempt']['code'] == 'source_policy_rejected' and not calls
    assert not approved_pdf_url('http://arxiv.org/pdf/1234') and not approved_pdf_url('https://arxiv.org.evil.test/pdf/1234')
    with pytest.raises(UnsafeUrlError):
        RestrictedRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/private')


def test_oa_status_without_a_recorded_url_is_not_a_policy_rejection(setup_web):
    create, factory = setup_web; client, job, paper_id, calls = create(sample_pdf())
    with factory() as session:
        session.get(Paper, paper_id).open_access_metadata = {'is_oa': True}; session.commit()
    result = client.post(f'/api/jobs/{job}/papers/{paper_id}/acquire').json()
    assert result['attempt']['code'] == 'source_unavailable' and not calls


def test_processing_lock_blocks_parallel_operations(setup_web):
    create, _ = setup_web; client, job, paper, calls = create(sample_pdf())
    PROCESSING_LOCK.acquire()
    try:
        assert client.post(f'/api/jobs/{job}/papers/{paper}/acquire').status_code == 409
        assert not calls
    finally: PROCESSING_LOCK.release()


def test_download_wall_clock_deadline(monkeypatch):
    monkeypatch.setattr('app.services.web_fulltext.monotonic', lambda: 50)
    response = DeadlineResponse(FakeResponse(b'%PDF-'), deadline=49)
    with pytest.raises(TimeoutError): response.read(5)


def test_public_https_handler_uses_verified_context_without_private_handler_attribute(monkeypatch):
    handler = PublicHTTPSHandler()
    captured = {}
    def fake_do_open(connection, request, **kwargs):
        captured.update(connection=connection, request=request, kwargs=kwargs)
        return 'opened'
    monkeypatch.setattr(handler, 'do_open', fake_do_open)
    assert handler.https_open('request') == 'opened'
    assert captured == {'connection': PublicHTTPSConnection, 'request': 'request', 'kwargs': {'context': handler._context}}


@pytest.mark.parametrize('error,code,stage,retryable,http_status', [
    (URLError(socket.gaierror('sensitive hostname')), 'dns_error', 'download', True, None),
    (URLError(ssl.SSLError('sensitive certificate')), 'tls_error', 'download', None, None),
    (URLError(ConnectionRefusedError('sensitive connection')), 'network_error', 'download', True, None),
    (HTTPError('https://secret.invalid', 503, 'sensitive body', {}, None), 'source_http_error', 'download', True, 503),
    (HTTPError('https://secret.invalid', 403, 'sensitive body', {}, None), 'source_http_error', 'download', False, 403),
    (TimeoutError('sensitive timeout'), 'download_timeout', 'download', True, None),
    (UnsafeUrlError('sensitive source'), 'source_policy_rejected', 'source_policy', False, None),
    (RuntimeError('sensitive unexpected'), 'unexpected_server_error', 'internal', None, None),
])
def test_attempt_failure_categories_persist_without_raw_errors(setup_web, error, code, stage, retryable, http_status):
    from app.models.research import DocumentProcessingAttempt, SourceDocument
    from sqlalchemy import select
    create, factory = setup_web; _client, job, paper_id, _ = create(sample_pdf())
    calls=[]
    def opener(*_args, **_kwargs):
        calls.append(1); raise error
    app = create_app(session_factory=factory, fulltext_downloader_factory=lambda: PdfDownloader(opener=opener))
    with TestClient(app, headers={'X-Blindspot-Request':'1'}) as api:
        result = api.post(f'/api/jobs/{job}/papers/{paper_id}/acquire').json()
        assert result['status'] == 'failed' and result['attempt']['code'] == code
        assert result['attempt']['stage'] == stage and result['attempt']['retryable'] is retryable
        assert result['attempt']['http_status'] == http_status and 'sensitive' not in str(result)
        again = api.get(f'/api/jobs/{job}/papers/{paper_id}/document').json()
        assert again['attempt']['code'] == code and len(calls) == 1
        with factory() as session:
            attempt = session.scalar(select(DocumentProcessingAttempt))
            assert attempt.research_job_id == job and attempt.paper_id == paper_id
            assert attempt.started_at and attempt.completed_at and attempt.outcome == 'failed'
            assert session.scalars(select(SourceDocument)).all() == []


def test_unexpected_acquisition_failure_has_opt_in_sanitized_traceback(setup_web, monkeypatch, caplog):
    create, factory = setup_web; _client, job, paper_id, _ = create(sample_pdf())
    monkeypatch.setenv('BLINDSPOT_LOG_DOCUMENT_EXCEPTIONS', 'true')
    caplog.set_level('ERROR', logger='app.services.web_fulltext')
    def opener(*_args, **_kwargs):
        raise RuntimeError('sensitive source response')
    app = create_app(session_factory=factory, fulltext_downloader_factory=lambda: PdfDownloader(opener=opener))
    with TestClient(app, headers={'X-Blindspot-Request':'1'}) as api:
        assert api.post(f'/api/jobs/{job}/papers/{paper_id}/acquire').json()['attempt']['code'] == 'unexpected_server_error'
    assert 'Blindspot PDF acquisition failed' in caplog.text
    assert caplog.records[-1].exc_info[1].args == ('RuntimeError',)


def test_manual_retry_success_is_recorded_and_complete_is_idempotent(setup_web):
    from app.models.research import DocumentProcessingAttempt, SourceDocument
    from sqlalchemy import select
    create, factory = setup_web; _client, job, paper_id, _ = create(sample_pdf())
    calls=[]
    def opener(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1: raise URLError(ConnectionRefusedError('private'))
        return FakeResponse(sample_pdf())
    app=create_app(session_factory=factory,fulltext_downloader_factory=lambda:PdfDownloader(opener=opener))
    with TestClient(app,headers={'X-Blindspot-Request':'1'}) as api:
        url=f'/api/jobs/{job}/papers/{paper_id}/acquire'
        assert api.post(url).json()['attempt']['code']=='network_error'
        assert api.post(url).json()['status']=='downloaded'
        assert api.post(url).json()['status']=='downloaded' and len(calls)==2
        with factory() as session:
            attempts=session.scalars(select(DocumentProcessingAttempt).order_by(DocumentProcessingAttempt.started_at)).all()
            assert [a.outcome for a in attempts]==['failed','succeeded']
            assert len(session.scalars(select(SourceDocument)).all())==1


def test_user_upload_recovers_from_nonretriable_acquisition_failure(setup_web):
    create, _ = setup_web; client, job, paper, _ = create(b'not a PDF')
    base=f'/api/jobs/{job}/papers/{paper}'
    assert client.post(base+'/acquire').json()['attempt']['code']=='invalid_pdf'
    assert client.post(base+'/upload',content=sample_pdf(),headers={'Content-Type':'application/pdf'}).json()['status']=='downloaded'
    result=client.post(base+'/extract').json()
    assert result['status']=='extracted' and result['chunk_count']==2


def test_missing_paper_or_header_does_not_create_attempt(setup_web):
    from app.models.research import DocumentProcessingAttempt
    from sqlalchemy import select
    create, factory = setup_web; client, job, paper_id, _ = create(sample_pdf())
    missing=client.post(f'/api/jobs/{job}/papers/other/acquire')
    assert missing.status_code == 404 and missing.headers['X-Blindspot-Error-Code']=='paper_not_found'
    assert missing.headers['X-Blindspot-Error-Stage']=='request_validation'
    assert client.post(f'/api/jobs/{job}/papers/{paper_id}/acquire',headers={'X-Blindspot-Request':'bad'}).status_code == 403
    with factory() as session: assert session.scalars(select(DocumentProcessingAttempt)).all()==[]


def test_prepare_shortlist_sequential_bounded_partial_and_idempotent(setup_web):
    create, factory = setup_web
    client, job, first_id, _ = create(sample_pdf())
    with factory() as session:
        first = session.query(ResearchJobPaper).filter_by(research_job_id=job, paper_id=first_id).one()
        first.included, first.relevance_score = True, 1.0
        for title, url, score, included in [
            ('Failure', 'https://arxiv.org/pdf/bad', .9, True),
            ('Unsupported', 'https://publisher.example/paper.pdf', .8, True),
            ('Second success', 'https://arxiv.org/pdf/good', .7, True),
            ('Unavailable', None, .6, True),
            ('Sixth shortlist', 'https://arxiv.org/pdf/sixth', .5, True),
            ('Not shortlisted', 'https://arxiv.org/pdf/other', 2, False),
        ]:
            paper = Paper(title=title, authors=[], open_access_metadata={'oa_url':url} if url else {})
            session.add(ResearchJobPaper(research_job_id=job, paper=paper, included=included, relevance_score=score))
        session.commit()
    calls=[]
    def opener(request, **_kwargs):
        calls.append(request.full_url)
        if request.full_url.endswith('/bad'): raise URLError('sensitive detail')
        return FakeResponse(sample_pdf())
    app = create_app(session_factory=factory, fulltext_downloader_factory=lambda: PdfDownloader(opener=opener))
    with TestClient(app, headers={'X-Blindspot-Request':'1'}) as api:
        result = api.post(f'/api/jobs/{job}/prepare-shortlist')
        assert result.status_code == 200 and result.json()['has_more'] is True
        assert [r['status'] for r in result.json()['results']] == ['extracted', 'failed', 'unsupported', 'extracted', 'unavailable']
        assert len(calls) == 3 and calls[1].endswith('/bad') and calls[2].endswith('/good')
        assert 'sensitive detail' not in result.text
        assert sum(len(d['chunks']) for d in api.get(f'/api/jobs/{job}/evidence').json()) == 4
        again = api.post(f'/api/jobs/{job}/prepare-shortlist').json()
        assert [r['status'] for r in again['results']] == ['extracted', 'failed', 'unsupported', 'extracted', 'unavailable']
        assert len(calls) == 3  # A later batch never silently retries a failed paper.
        assert sum(len(d['chunks']) for d in api.get(f'/api/jobs/{job}/evidence').json()) == 4


def test_prepare_shortlist_no_eligible_and_overlap_lock(setup_web):
    create, factory = setup_web; client, job, paper_id, calls = create(sample_pdf())
    with factory() as session:
        paper = session.get(Paper, paper_id); paper.open_access_metadata = {'oa_url':'https://publisher.example/paper.pdf'}
        link = session.query(ResearchJobPaper).filter_by(research_job_id=job, paper_id=paper_id).one(); link.included = True
        session.commit()
    response = client.post(f'/api/jobs/{job}/prepare-shortlist').json()
    assert response['results'][0]['status'] == 'unsupported' and not calls
    PROCESSING_LOCK.acquire()
    try: assert client.post(f'/api/jobs/{job}/prepare-shortlist').status_code == 409
    finally: PROCESSING_LOCK.release()


def test_oa_resolution_requires_explicit_trusted_pdf_and_prioritizes_repository():
    from app.services.web_fulltext import source_url
    paper = Paper(title='OA', authors=[], open_access_metadata={'pdf_locations': [
        {'is_oa': True, 'source_type': 'journal', 'pdf_url': 'https://journals.plos.org/plosone/article/file?id=x&type=printable'},
        {'is_oa': True, 'source_type': 'repository', 'pdf_url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/article.pdf'},
        {'is_oa': False, 'source_type': 'repository', 'pdf_url': 'https://arxiv.org/pdf/unapproved'},
    ]})
    assert source_url(paper) == 'https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/article.pdf'
    paper.open_access_metadata = {'is_oa': True, 'oa_url': 'https://pmc.ncbi.nlm.nih.gov/articles/PMC123/'}
    assert source_url(paper) is None
    for url in ['https://pmc.ncbi.nlm.nih.gov.evil.test/articles/PMC123/pdf/x', 'https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/../private', 'https://journals.plos.org/plosone/article/file?id=x', 'https://127.0.0.1/pdf/x']:
        assert not approved_pdf_url(url)
        with pytest.raises(UnsafeUrlError): RestrictedRedirect().redirect_request(None, None, 302, '', {}, url)


def test_dns_rejects_private_or_mixed_addresses(monkeypatch):
    from app.services.web_fulltext import public_addresses
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(UnsafeUrlError): public_addresses('arxiv.org', 443)


def test_upload_extract_repeat_and_job_isolation(setup_web):
    create, factory = setup_web; client, job, paper, calls = create(sample_pdf())
    base = f'/api/jobs/{job}/papers/{paper}'
    headers = {'Content-Type': 'application/pdf'}
    content = sample_pdf()
    result = client.post(base + '/upload', content=content, headers=headers)
    assert result.status_code == 200 and result.json()['status'] == 'downloaded'
    assert result.json()['reused'] is False and 'not independently verified' in result.json()['source']
    assert client.post(base + '/upload', content=content, headers=headers).json()['reused'] is True
    assert client.post(base + '/extract').json()['status'] == 'extracted'
    document = client.get(f'/api/jobs/{job}/evidence').json()[0]
    assert document['origin'] == 'user_upload_unverified' and not document['claims_available']
    assert [c['page'] for c in document['chunks']] == [1, 2]
    assert not calls
    other = client.post('/api/jobs', json={'topic': 'another job'}).json()['id']
    assert client.post(f'/api/jobs/{other}/papers/{paper}/upload', content=sample_pdf(), headers=headers).status_code == 404
    with factory() as session:
        assert len(session.get(Paper, paper).source_documents) == 1


@pytest.mark.parametrize('content', [b'not a pdf', b'%PDF-not-a-valid-document'])
def test_upload_rejects_invalid_pdf(setup_web, content):
    create, _ = setup_web; client, job, paper, _ = create(sample_pdf())
    result = client.post(f'/api/jobs/{job}/papers/{paper}/upload', content=content, headers={'Content-Type': 'application/pdf'})
    assert result.status_code == 422
    assert client.get(f'/api/jobs/{job}/evidence').json() == []


def test_upload_size_page_and_existing_document_guards(setup_web, monkeypatch):
    create, _ = setup_web; client, job, paper, _ = create(sample_pdf())
    base = f'/api/jobs/{job}/papers/{paper}/upload'; headers = {'Content-Type': 'application/pdf'}
    monkeypatch.setattr('app.services.web_fulltext.MAX_PDF_BYTES', 10)
    assert client.post(base, content=sample_pdf(), headers=headers).status_code == 413
    monkeypatch.setattr('app.services.web_fulltext.MAX_PDF_BYTES', 20 * 1024 * 1024)
    monkeypatch.setattr('app.services.web_fulltext.MAX_PAGES', 1)
    assert client.post(base, content=sample_pdf(), headers=headers).status_code == 422
    monkeypatch.setattr('app.services.web_fulltext.MAX_PAGES', 300)
    assert client.post(base, content=sample_pdf(), headers=headers).status_code == 200
    pdf = fitz.open(); pdf.new_page(); other = pdf.tobytes(); pdf.close()
    assert client.post(base, content=other, headers=headers).status_code == 409


def identity_pdf(title, *, metadata_title=None, metadata_doi=None):
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72, 72), title)
    metadata = pdf.metadata
    metadata['title'] = metadata_title or ''
    metadata['subject'] = 'doi:' + metadata_doi if metadata_doi else ''
    pdf.set_metadata(metadata)
    data = pdf.tobytes(); pdf.close(); return data


@pytest.mark.parametrize('pdf_title,metadata_title,metadata_doi,expected', [
    ('Example Scientific Research Paper', 'Example Scientific Research Paper', '10.1234/example', 'identity_review_required'),
    ('Unrelated Paper Title Here', '', None, 'identity_review_required'),
    ('Example Scientific Research Paper', 'Different Scientific Paper Title', None, 'identity_conflict'),
    ('Example Scientific Research Paper', 'Example Scientific Research Paper', '10.1234/different', 'identity_conflict'),
])
def test_upload_identity_review_requires_human_and_rejects_conflicts(setup_web, pdf_title, metadata_title, metadata_doi, expected):
    create, factory = setup_web; client, job, paper_id, _ = create(sample_pdf())
    with factory() as session:
        paper = session.get(Paper, paper_id); paper.title = 'Example Scientific Research Paper'; paper.doi = '10.1234/example'; session.commit()
    base = f'/api/jobs/{job}/papers/{paper_id}'
    upload = client.post(base + '/upload', content=identity_pdf(pdf_title, metadata_title=metadata_title, metadata_doi=metadata_doi), headers={'Content-Type':'application/pdf'}).json()
    assert upload['status'] == 'downloaded'
    endpoint = base + '/documents/' + upload['document_id'] + '/identity'
    assessment = client.get(endpoint).json()
    assert assessment['status'] == expected
    assert client.post(endpoint, json={'confirm_association':False, 'review_token':assessment['review_token']}).status_code == 403
    response = client.post(endpoint, json={'confirm_association':True, 'review_token':assessment['review_token']})
    if assessment['can_confirm']:
        assert response.status_code == 200 and response.json()['status'] == 'human_confirmed'
        assert 'not independently verified' in response.json()['message']
        assert client.post(endpoint, json={'confirm_association': True, 'review_token': assessment['review_token']}).status_code == 200
        assert client.post(base + '/extract').json()['status'] == 'extracted'
        assert client.get(f'/api/jobs/{job}/evidence').json()[0]['claims_available'] is True
        from app.models.research import UploadIdentityReview
        from sqlalchemy import select
        with factory() as session:
            reviews = session.scalars(select(UploadIdentityReview)).all()
            assert len(reviews) == 1 and reviews[0].research_job_id == job
            assert reviews[0].reviewer_action == 'local_user_confirmed_association'
            assert reviews[0].document_hash and reviews[0].reviewed_at
            from app.models.research import SourceDocument
            from app.services.web_claims import ensure_processed_source
            document = session.get(SourceDocument, upload['document_id'])
            ensure_processed_source(document.evidence_chunks[0], document)
            session.get(Paper, paper_id).title = 'Changed paper title after review'; session.commit()
        assert not client.get(f'/api/jobs/{job}/evidence').json()[0]['claims_available']
    else:
        assert response.status_code == 409


def test_upload_identity_changes_with_file_hash(setup_web):
    create, factory = setup_web; client, job, paper_id, _ = create(sample_pdf())
    with factory() as session:
        session.get(Paper, paper_id).title = 'Example Scientific Research Paper'; session.commit()
    base = f'/api/jobs/{job}/papers/{paper_id}'
    upload = client.post(base + '/upload', content=identity_pdf('Example Scientific Research Paper'), headers={'Content-Type':'application/pdf'}).json()
    endpoint = base + '/documents/' + upload['document_id'] + '/identity'
    assessment = client.get(endpoint).json()
    assert client.post(endpoint, json={'confirm_association':True, 'review_token':assessment['review_token']}).status_code == 200
    with factory() as session:
        from app.models.research import SourceDocument
        from pathlib import Path
        document = session.get(SourceDocument, upload['document_id']); path = Path(document.local_path)
        path.write_bytes(path.read_bytes() + b'changed')
    assert client.get(endpoint).status_code == 409


def test_identity_review_rejects_stale_metadata_and_other_job(setup_web):
    create, factory = setup_web; client, job, paper_id, _ = create(sample_pdf())
    with factory() as session:
        session.get(Paper, paper_id).title = 'Example Scientific Research Paper'; session.commit()
    base = f'/api/jobs/{job}/papers/{paper_id}'
    upload = client.post(base + '/upload', content=identity_pdf('Example Scientific Research Paper'), headers={'Content-Type':'application/pdf'}).json()
    endpoint = base + '/documents/' + upload['document_id'] + '/identity'
    token = client.get(endpoint).json()['review_token']
    other = client.post('/api/jobs', json={'topic':'Different job'}).json()['id']
    assert client.get(endpoint.replace(job, other)).status_code == 404
    with factory() as session:
        session.get(Paper, paper_id).doi = '10.1234/new'; session.commit()
    assert client.post(endpoint, json={'confirm_association':True, 'review_token':token}).status_code == 409

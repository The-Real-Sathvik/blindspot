"""Bounded single-paper web workflow using the existing PDF processor.

Only stored metadata pointing to approved PDF endpoints is accepted.
"""
from pathlib import Path
import http.client
import ipaddress
import logging
import os
import re
import socket
from threading import Lock
from time import monotonic
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener
from uuid import uuid4

import pymupdf as fitz
from sqlalchemy import select

from app.models.research import SourceDocument
from app.schemas.research import DocumentStatus
from app.services.fulltext import DownloadError, ExtractionError, FullTextService, MAX_PDF_BYTES, PdfDownloader, SOURCE_DIR, UnsafeUrlError, classify_download_exception

MAX_PAGES = 300
DOWNLOAD_DEADLINE_SECONDS = 45
PROCESSING_LOCK = Lock()
logger = logging.getLogger(__name__)


def _log_development_acquisition_exception(error: Exception, *, paper_id: str) -> None:
    """Emit a local traceback only when a developer explicitly enables it.

    The durable attempt remains sanitized; the log intentionally substitutes the
    exception value so arbitrary source messages, URLs, and response content are
    not emitted.
    """
    if os.getenv('BLINDSPOT_LOG_DOCUMENT_EXCEPTIONS', '').strip().casefold() != 'true':
        return
    safe_error = RuntimeError(type(error).__name__)
    logger.exception('Blindspot PDF acquisition failed (paper_id=%s).', paper_id,
                     exc_info=(RuntimeError, safe_error, error.__traceback__))


def approved_pdf_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username
                or parsed.password or parsed.fragment or '%' in parsed.path or '\\' in parsed.path
                or any(part in {'.', '..'} for part in parsed.path.split('/'))):
            return False
        host, path = parsed.hostname, parsed.path
        return bool((host in {'arxiv.org', 'export.arxiv.org'} and path.startswith('/pdf/') and len(path) > 5)
            or (host == 'pmc.ncbi.nlm.nih.gov' and re.fullmatch(r'/articles/PMC[0-9]+/pdf/[^/]+', path))
            or (host == 'journals.plos.org' and re.fullmatch(r'/[^/]+/article/file', path)
                and 'type=printable' in parsed.query.split('&')))
    except (ValueError, TypeError):
        return False


def source_url(paper):
    candidates = []
    for identifier in paper.external_identifiers:
        if identifier.source_name == 'arxiv' and isinstance(identifier.source_metadata, dict):
            candidates.append(identifier.source_metadata.get('pdf_url'))
    if isinstance(paper.open_access_metadata, dict):
        metadata = paper.open_access_metadata
        # Explicit location-level OA status is required. Repository before publisher.
        locations = metadata.get('pdf_locations')
        if isinstance(locations, list):
            for kind in ('repository', 'journal'):
                for location in locations[:20]:
                    if isinstance(location, dict) and location.get('is_oa') is True and location.get('source_type') == kind:
                        candidates.append(location.get('pdf_url'))
        # Existing arXiv URLs remain supported; other OA URLs require an explicit
        # open status and must have the shape of an approved direct PDF endpoint.
        oa_url = metadata.get('oa_url')
        if isinstance(oa_url, str):
            try:
                if metadata.get('is_oa') is True or urlparse(oa_url).hostname in {'arxiv.org', 'export.arxiv.org'}:
                    candidates.append(oa_url)
            except ValueError:
                pass
    return next((u for u in candidates if isinstance(u, str) and approved_pdf_url(u)), None)


def has_other_source(paper):
    return bool((isinstance(paper.open_access_metadata, dict) and (paper.open_access_metadata.get('oa_url') or paper.open_access_metadata.get('pdf_locations') or paper.open_access_metadata.get('is_oa'))) or any(i.source_name == 'arxiv' and isinstance(i.source_metadata, dict) and i.source_metadata.get('pdf_url') for i in paper.external_identifiers))


def has_recorded_source_url(paper):
    metadata = paper.open_access_metadata if isinstance(paper.open_access_metadata, dict) else {}
    if isinstance(metadata.get('oa_url'), str) and metadata['oa_url'].strip(): return True
    locations = metadata.get('pdf_locations')
    if isinstance(locations, list) and any(isinstance(item, dict) and isinstance(item.get('pdf_url'), str) and item['pdf_url'].strip() for item in locations[:20]): return True
    return any(i.source_name == 'arxiv' and isinstance(i.source_metadata, dict) and isinstance(i.source_metadata.get('pdf_url'), str) and i.source_metadata['pdf_url'].strip() for i in paper.external_identifiers)


def source_label(url):
    host = urlparse(url).hostname if url else None
    return {'arxiv.org': 'arXiv repository', 'export.arxiv.org': 'arXiv repository',
            'pmc.ncbi.nlm.nih.gov': 'PubMed Central repository',
            'journals.plos.org': 'PLOS publisher'}.get(host)


def public_addresses(host, port):
    addresses = []
    for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(entry[4][0].split('%')[0])
        mapped = getattr(address, 'ipv4_mapped', None)
        if not address.is_global or address.is_multicast or (mapped and (not mapped.is_global or mapped.is_multicast)):
            raise UnsafeUrlError('PDF host resolved to a non-public address.')
        addresses.append(str(address))
    if not addresses:
        raise UnsafeUrlError('PDF host has no public address.')
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # Connect to the validated numeric address, preventing DNS rebinding.
        addresses = public_addresses(self.host, self.port)
        self.sock = socket.create_connection((addresses[0], self.port), self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        except Exception:
            self.sock.close()
            raise


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        # Python 3.12's HTTPSHandler exposes its verified SSL context but no
        # `_check_hostname` attribute. Passing that absent implementation detail
        # prevented every outbound HTTPS acquisition before a request was sent.
        return self.do_open(PublicHTTPSConnection, request, context=self._context)


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not approved_pdf_url(newurl):
            raise UnsafeUrlError('PDF redirect is outside the supported PDF sources.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class DeadlineResponse:
    def __init__(self, response, deadline):
        self._response, self._deadline = response, deadline
        self.headers = response.headers
    def geturl(self): return self._response.geturl()
    def read(self, amount):
        if monotonic() >= self._deadline: raise TimeoutError('PDF download deadline exceeded.')
        data = self._response.read(amount)
        if monotonic() >= self._deadline: raise TimeoutError('PDF download deadline exceeded.')
        return data
    def __enter__(self): return self
    def __exit__(self, *_args): self._response.close()


def bounded_opener(opener):
    def open_pdf(request, timeout):
        deadline = monotonic() + DOWNLOAD_DEADLINE_SECONDS
        return DeadlineResponse(opener.open(request, timeout=timeout), deadline)
    return open_pdf


def safe_local_file(document):
    if not document.local_path: return None
    try:
        root = SOURCE_DIR.resolve()
        path = Path(document.local_path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > MAX_PDF_BYTES:
            return None
        return path
    except (OSError, ValueError, RuntimeError):
        return None


def save_upload(session, paper, path, digest):
    """Associate a validated local upload without replacing an existing PDF."""
    for doc in paper.source_documents:
        if safe_local_file(doc):
            if doc.content_hash == digest:
                return document_state(paper), True
            raise ValueError('A saved PDF already exists for this paper; it was not replaced.')
    document = SourceDocument(paper=paper, source_url='user-upload:unverified', document_type='pdf',
                              status=DocumentStatus.PENDING, local_path=str(path), content_hash=digest)
    session.add(document)
    session.flush()
    return document_state(paper), False


def validate_upload(path):
    try:
        with path.open('rb') as file:
            if file.read(5) != b'%PDF-': raise ValueError('Invalid PDF signature.')
        # Opening bytes avoids a MuPDF file-handle leak on malformed PDFs on Windows.
        with fitz.open(stream=path.read_bytes(), filetype='pdf') as pdf:
            if pdf.needs_pass or pdf.is_encrypted or pdf.is_repaired or not 1 <= pdf.page_count <= MAX_PAGES:
                raise ValueError('PDF is encrypted, damaged, empty, or exceeds the page limit.')
    except (fitz.FileDataError, OSError, RuntimeError):
        raise ValueError('Invalid PDF structure.') from None


def document_state(paper):
    docs = sorted(paper.source_documents, key=lambda d: (d.created_at is not None, d.created_at, d.id), reverse=True)
    for doc in docs:
        if doc.evidence_chunks:
            return {'status': 'extracted', 'document_id': doc.id, 'chunk_count': len(doc.evidence_chunks), 'can_acquire': False, 'can_extract': False, 'source': 'User-uploaded PDF; paper identity not independently verified' if doc.source_url == 'user-upload:unverified' else 'Saved PDF', 'message': 'Page-linked evidence is available.'}
    for doc in docs:
        if safe_local_file(doc):
            if doc.status == DocumentStatus.EXTRACTION_FAILED:
                return {'status': 'failed', 'document_id': doc.id, 'chunk_count': 0, 'can_acquire': False, 'can_extract': True, 'source': 'User-uploaded PDF; paper identity not independently verified' if doc.source_url == 'user-upload:unverified' else 'Saved PDF', 'message': 'Evidence extraction failed; the saved PDF can be retried.'}
            return {'status': 'downloaded', 'document_id': doc.id, 'chunk_count': 0, 'can_acquire': False, 'can_extract': True, 'source': 'User-uploaded PDF; paper identity not independently verified' if doc.source_url == 'user-upload:unverified' else 'Saved PDF', 'message': 'Local PDF available for evidence extraction.'}
    for doc in docs:
        if doc.status in {DocumentStatus.EXTRACTION_FAILED, DocumentStatus.RETRIABLE_ERROR}:
            return {'status': 'failed', 'document_id': doc.id, 'chunk_count': 0, 'can_acquire': bool(source_url(paper)), 'can_extract': False, 'message': 'Document processing failed; retry if an eligible source is available.'}
    url = source_url(paper)
    if url:
        return {'status': 'available_for_acquisition', 'document_id': None, 'chunk_count': 0, 'can_acquire': True, 'can_extract': False, 'source': source_label(url), 'message': 'A supported PDF location is recorded; access and content have not been verified.'}
    if has_other_source(paper):
        return {'status': 'unsupported', 'document_id': None, 'chunk_count': 0, 'can_acquire': False, 'can_extract': False, 'message': 'No supported direct PDF endpoint is recorded. An OA landing page alone is not a PDF.'}
    return {'status': 'unavailable', 'document_id': None, 'chunk_count': 0, 'can_acquire': False, 'can_extract': False, 'message': 'No supported full-text PDF link is recorded.'}


def acquire(session, paper, *, downloader=None):
    state = document_state(paper)
    if state['status'] in {'downloaded', 'extracted'}: return state
    url = source_url(paper)
    if not url:
        state['_failure'] = {'code': 'source_policy_rejected' if has_recorded_source_url(paper) else 'source_unavailable', 'stage': 'source_policy', 'retryable': False, 'http_status': None}
        return state
    document = session.scalar(select(SourceDocument).where(SourceDocument.paper_id == paper.id, SourceDocument.source_url == url).order_by(SourceDocument.created_at.desc()))
    target = SOURCE_DIR / f'{uuid4().hex}.pdf'
    try:
        # A unique destination avoids overwriting any pre-existing user file.
        if downloader is None:
            opener = build_opener(ProxyHandler({}), RestrictedRedirect(), PublicHTTPSHandler())
            downloader = PdfDownloader(timeout_seconds=30, opener=bounded_opener(opener))
        downloaded = downloader.download(url, target)
        if not approved_pdf_url(downloaded.final_url):
            raise UnsafeUrlError('Final PDF source is not permitted.')
        try:
            validate_upload(downloaded.local_path)
        except ValueError:
            raise DownloadError('Downloaded PDF is invalid.', code='invalid_pdf', stage='pdf_validation', retryable=False) from None
        if document is None:
            document = SourceDocument(paper=paper, source_url=url, document_type='pdf', status=DocumentStatus.PENDING)
            session.add(document)
        document.local_path, document.content_hash = str(downloaded.local_path), downloaded.content_hash
        document.source_url, document.status, document.error_info = downloaded.final_url, DocumentStatus.PENDING, None
        session.flush()
    except Exception as error:
        _log_development_acquisition_exception(error, paper_id=paper.id)
        # This exact path was uniquely created for the failed attempt only.
        target.unlink(missing_ok=True)
        failure = classify_download_exception(error)
        if document is not None:
            document.status, document.error_info = DocumentStatus.RETRIABLE_ERROR, 'PDF acquisition failed or source was rejected.'
            session.flush()
        state = document_state(paper)
        state['status'] = 'failed'
        state['_failure'] = {'code': failure.code, 'stage': failure.stage, 'retryable': failure.retryable, 'http_status': failure.http_status}
        return state
    return document_state(paper)


def extract(session, paper):
    state = document_state(paper)
    if state['status'] == 'extracted': return state
    if not state['can_extract']: return state
    document = session.get(SourceDocument, state['document_id'])
    path = safe_local_file(document)
    try:
        if path is None: raise ExtractionError('Local PDF is unavailable or exceeds the size limit.')
        with fitz.open(path) as pdf:
            if pdf.page_count > MAX_PAGES: raise ExtractionError('PDF exceeds the page limit.')
        FullTextService()._extract(document)
        document.status, document.error_info = DocumentStatus.AVAILABLE, None
    except (ExtractionError, fitz.FileDataError, OSError, ValueError, RuntimeError):
        # No partial page chunks are kept after a failed extraction.
        document.evidence_chunks.clear()
        document.status, document.error_info = DocumentStatus.EXTRACTION_FAILED, 'PDF evidence extraction failed.'
        session.flush()
        state = document_state(paper)
        state['_failure'] = {'code': 'pdf_extraction_failed', 'stage': 'extraction', 'retryable': None, 'http_status': None}
        return state
    session.flush()
    return document_state(paper)

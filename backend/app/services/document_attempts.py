"""Durable, job-specific document attempt outcomes; never store exception text."""
from datetime import datetime, timezone
import logging
import os
from sqlalchemy import select

from app.models.research import DocumentProcessingAttempt
from app.services.web_fulltext import acquire, document_state, extract

logger = logging.getLogger(__name__)


def _log_development_exception(error: Exception, *, operation: str, paper_id: str) -> None:
    """Opt-in traceback for local diagnosis without recording exception text.

    Passing a replacement exception value preserves the original traceback while
    preventing arbitrary downloader/provider exception messages from reaching
    the console. This is deliberately disabled unless the local developer opts in.
    """
    if os.getenv('BLINDSPOT_LOG_DOCUMENT_EXCEPTIONS', '').strip().casefold() != 'true':
        return
    safe_error = RuntimeError(type(error).__name__)
    logger.exception('Blindspot document attempt failed (operation=%s, paper_id=%s).', operation, paper_id,
                     exc_info=(RuntimeError, safe_error, error.__traceback__))

MESSAGES = {
    'source_policy_rejected': 'The recorded source is not permitted by the PDF security policy.',
    'source_unavailable': 'No supported PDF source is recorded.',
    'dns_error': 'The PDF source hostname could not be resolved.',
    'tls_error': 'A secure connection to the PDF source could not be established.',
    'network_error': 'The PDF source connection failed.',
    'source_http_error': 'The PDF source returned an HTTP error.',
    'download_timeout': 'The PDF download timed out.',
    'invalid_pdf': 'The downloaded content was not a valid PDF.',
    'pdf_too_large': 'The PDF exceeded the configured size limit.',
    'pdf_extraction_failed': 'Evidence extraction failed for the saved PDF.',
    'unexpected_server_error': 'The server could not complete document processing.',
}


def latest_attempt(session, job_id, paper_id):
    return session.scalar(select(DocumentProcessingAttempt).where(
        DocumentProcessingAttempt.research_job_id == job_id,
        DocumentProcessingAttempt.paper_id == paper_id,
    ).order_by(DocumentProcessingAttempt.started_at.desc(), DocumentProcessingAttempt.id.desc()).limit(1))


def job_document_state(session, job_id, paper):
    state = document_state(paper)
    attempt = latest_attempt(session, job_id, paper.id)
    if attempt is None or attempt.outcome == 'succeeded': return state
    if attempt.outcome == 'started':
        state['attempt'] = {'outcome':'started', 'stage':attempt.stage, 'code':None, 'retryable':None, 'started_at':attempt.started_at}
        return state
    failure = {'outcome':'failed', 'operation':attempt.operation, 'stage':attempt.stage, 'code':attempt.error_code,
               'retryable':attempt.retryable, 'http_status':attempt.http_status,
               'started_at':attempt.started_at, 'completed_at':attempt.completed_at}
    # Never conceal a completed or subsequently uploaded document behind an old acquisition error.
    if state['status'] == 'extracted' or (state['status'] == 'downloaded' and attempt.operation == 'acquire'):
        state['previous_attempt'] = failure
        return state
    state['attempt'] = failure
    state['status'] = 'failed'
    state['message'] = MESSAGES.get(attempt.error_code, MESSAGES['unexpected_server_error'])
    if attempt.retryable is False:
        if attempt.operation == 'acquire': state['can_acquire'] = False
        if attempt.operation == 'extract': state['can_extract'] = False
    return state


def perform_attempt(session, job_id, paper, operation, *, downloader_factory=None):
    before = job_document_state(session, job_id, paper)
    # Reusing completed work does not create a new attempt or touch the network.
    if operation == 'acquire' and (before['status'] in {'downloaded', 'extracted'} or before['can_extract']): return before
    if operation == 'extract' and before['status'] == 'extracted': return before
    if operation == 'extract' and not before['can_extract']: return before
    if before.get('attempt', {}).get('outcome') == 'failed' and before['attempt']['operation'] == operation and before['attempt']['retryable'] is False:
        return before
    row = DocumentProcessingAttempt(research_job_id=job_id, paper_id=paper.id, operation=operation,
        outcome='started', stage='source_policy' if operation == 'acquire' else 'extraction',
        started_at=datetime.now(timezone.utc))
    session.add(row); session.commit()  # The backend has accepted the attempt; survives process failure.
    try:
        result = acquire(session, paper, downloader=downloader_factory() if downloader_factory else None) if operation == 'acquire' else extract(session, paper)
        failure = result.pop('_failure', None)
        if failure is None and result['status'] == 'failed':
            failure = {'code':'unexpected_server_error', 'stage':'internal', 'retryable':None, 'http_status':None}
        if failure:
            row.outcome, row.error_code, row.stage = 'failed', failure['code'], failure['stage']
            row.retryable, row.http_status = failure['retryable'], failure['http_status']
        else:
            row.outcome, row.stage = 'succeeded', operation
        row.completed_at = datetime.now(timezone.utc)
        session.commit()
    except Exception as error:
        _log_development_exception(error, operation=operation, paper_id=paper.id)
        session.rollback()
        row = session.get(DocumentProcessingAttempt, row.id)
        row.outcome, row.stage, row.error_code = 'failed', 'internal', 'unexpected_server_error'
        row.retryable, row.completed_at = None, datetime.now(timezone.utc)
        session.commit()
    return job_document_state(session, job_id, paper)

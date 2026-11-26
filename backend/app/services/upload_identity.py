"""Conservative metadata-assisted review of an uploaded PDF's association.

Matching metadata is supporting context, not independent scientific verification.
The first-page title check only accepts the expected title at the start of the page.
DOIs are read only from explicit PDF metadata, never inferred from reference lists.
"""
from datetime import datetime, timezone
from hashlib import sha256
import re
import json
import unicodedata

import pymupdf as fitz

from app.models.research import UploadIdentityReview
from app.services.web_fulltext import safe_local_file


def title_key(value):
    value = unicodedata.normalize('NFKC', value or '').casefold()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value).split())


def doi_key(value):
    value = (value or '').strip().casefold()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value)
    return value.rstrip('.,;') if re.fullmatch(r'10\.\d{4,9}/\S+', value) else None


def assess(document):
    path = safe_local_file(document)
    if document.source_url != 'user-upload:unverified' or path is None:
        raise ValueError('An available user-uploaded PDF is required.')
    data = path.read_bytes()
    digest = sha256(data).hexdigest()
    if not document.content_hash or digest != document.content_hash:
        raise ValueError('Stored PDF hash does not match the upload.')
    with fitz.open(stream=data, filetype='pdf') as pdf:
        metadata = pdf.metadata or {}
        title = (metadata.get('title') or '').strip()[:1000]
        author = (metadata.get('author') or '').strip()[:500]
        if title_key(title) in {'untitled', 'document', 'microsoft word', 'pdf', ''}: title = ''
        header = pdf[0].get_text('text')[:1200] if pdf.page_count else ''
        metadata_dois = {doi_key(metadata.get(field)) for field in ('subject', 'keywords')}
        metadata_dois.discard(None)
    expected_title = title_key(document.paper.title)
    embedded_key = title_key(title)
    # Avoid confirming a generic or trivially short title.
    meaningful = len(expected_title) >= 20 and len(expected_title.split()) >= 3
    title_match = bool(meaningful and (embedded_key == expected_title or title_key(header).startswith(expected_title)))
    title_conflict = bool(embedded_key and embedded_key != expected_title)
    paper_doi = doi_key(document.paper.doi)
    doi_conflict = bool(paper_doi and metadata_dois and metadata_dois != {paper_doi})
    conflict = title_conflict or doi_conflict or len(metadata_dois) > 1
    comparison = {'paper_title': document.paper.title, 'paper_doi': paper_doi,
                  'pdf_title': title or None, 'pdf_author': author or None,
                  'pdf_dois': sorted(metadata_dois), 'title_match': title_match,
                  'title_conflict': title_conflict, 'doi_conflict': doi_conflict}
    eligible = title_match and not conflict
    confirmed = eligible and any(r.outcome == 'human_confirmed' and r.document_hash == digest
                                 and r.comparison == comparison for r in document.identity_reviews)
    review_token = sha256(json.dumps({'hash': digest, 'comparison': comparison}, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {'status': 'human_confirmed' if confirmed else 'identity_conflict' if conflict else 'identity_review_required',
            'can_confirm': eligible and not confirmed, 'comparison': comparison,
            'first_page_header': header[:800], 'document_hash': digest, 'review_token': review_token,
            'message': 'Human-confirmed association; not independently verified.' if confirmed else
                       'Metadata conflicts with the selected paper; confirmation is blocked.' if conflict else
                       'Review the title and identifiers before confirming.' if eligible else
                       'Insufficient matching title metadata; confirmation is blocked.'}


def confirm(session, document, job_id, review_token):
    result = assess(document)
    if result['review_token'] != review_token:
        raise ValueError('Document or paper metadata changed; review it again before confirming.')
    if result['status'] == 'human_confirmed': return result
    if not result['can_confirm']: raise ValueError(result['message'])
    session.add(UploadIdentityReview(document=document, research_job_id=job_id,
        outcome='human_confirmed', reviewer_action='local_user_confirmed_association',
        document_hash=result['document_hash'], comparison=result['comparison'],
        reviewed_at=datetime.now(timezone.utc)))
    session.flush()
    return assess(document)

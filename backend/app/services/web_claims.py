"""Web claim extraction source checks; never loads evaluation answers into prompts."""
from hashlib import sha256
import json
from pathlib import Path

import pymupdf as fitz
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import object_session

from app.schemas.research import DocumentStatus
from app.services.fulltext import _normalise_text
from app.services.web_fulltext import safe_local_file
from app.services.upload_identity import assess
from app.models.research import SourceDocument

HELDOUT_MANIFEST = Path(__file__).resolve().parents[2] / 'tests' / 'fixtures' / 'claim_extraction_reference_annotations_phase_4b_3a_v2.json'


def heldout_document_ids():
    try:
        entries = json.loads(HELDOUT_MANIFEST.read_text(encoding='utf-8'))
        if not isinstance(entries, list) or not entries:
            raise ValueError('Missing evaluation split')
        if any(not isinstance(item, dict) or item.get('dataset_split') not in {'development', 'held_out'} or not isinstance(item.get('source_document_id'), str) for item in entries):
            raise ValueError('Invalid evaluation split')
        development = {item['source_document_id'] for item in entries if item['dataset_split'] == 'development'}
        heldout = {item['source_document_id'] for item in entries if item['dataset_split'] == 'held_out'}
        if not development or not heldout or development & heldout or '' in (development | heldout):
            raise ValueError('Evaluation documents are not separated')
        return frozenset(heldout)
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, 'Evaluation partition cannot be verified; extraction is blocked.') from None


def ensure_processed_source(chunk, document):
    heldout = heldout_document_ids()
    if document.id in heldout:
        raise HTTPException(403, 'Held-out evaluation documents are excluded from live extraction.')
    if document.source_url == 'user-upload:unverified':
        session = object_session(document)
        if session is None:
            raise HTTPException(409, 'Upload provenance cannot be checked.')
        hashes = set(session.scalars(select(SourceDocument.content_hash).where(SourceDocument.id.in_(heldout), SourceDocument.content_hash.is_not(None))))
        if document.content_hash and document.content_hash in hashes:
            raise HTTPException(403, 'Held-out evaluation documents are excluded from live extraction.')
        try:
            if assess(document)['status'] != 'human_confirmed':
                raise ValueError('Unconfirmed association')
        except (ValueError, OSError, RuntimeError, fitz.FileDataError):
            raise HTTPException(409, 'Uploaded PDF association requires human confirmation without metadata conflicts.') from None
    path = safe_local_file(document)
    if document.document_type != 'pdf' or document.status != DocumentStatus.AVAILABLE or path is None:
        raise HTTPException(409, 'A processed local PDF is required for this evidence chunk.')
    try:
        if document.content_hash:
            digest = sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(65536), b''): digest.update(block)
            if digest.hexdigest() != document.content_hash:
                raise ValueError('PDF hash mismatch')
        with fitz.open(path) as pdf:
            if chunk.page_number < 1 or chunk.page_number > pdf.page_count:
                raise ValueError('Invalid PDF page')
            page = _normalise_text(pdf[chunk.page_number - 1].get_text('text'))
            if not (0 <= chunk.start_offset < chunk.end_offset <= len(page)) or page[chunk.start_offset:chunk.end_offset] != chunk.text:
                raise ValueError('Source text mismatch')
            return page
    except (OSError, ValueError, RuntimeError, fitz.FileDataError):
        raise HTTPException(409, 'Stored PDF and evidence provenance do not match; extraction is blocked.') from None

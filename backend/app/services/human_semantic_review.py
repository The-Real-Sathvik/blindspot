"""Explicit human assessments; a literal match is never a semantic judgment.

The existing one-to-one VerificationResult stores a bounded, versioned review
envelope. Reviews are immutable in this phase; replacing/revoking requires an
auditable history design. Reviewer identity is self-reported, not authenticated.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.models.research import VerificationResult
from app.schemas.research import CitationCheckStatus, SemanticSupportStatus
from app.services.web_claims import ensure_processed_source

VERSION = 'human-semantic-review-v1'


class HumanReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    semantic_status: SemanticSupportStatus
    reviewer: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=2000)
    reviewed_evidence: StrictBool

    @field_validator('reviewer', 'rationale')
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise ValueError('A nonempty reviewer and rationale are required.')
        return value


def evidence_snapshot(claim):
    if not claim.evidence_links:
        raise ValueError('A claim requires grounded evidence.')
    entries=[]
    for evidence in claim.evidence_links:
        chunk=evidence.evidence_chunk; doc=chunk.source_document
        if (doc.paper_id != claim.paper_id or evidence.quotation_status != CitationCheckStatus.VALID
            or evidence.source_location_status != CitationCheckStatus.VALID
            or not 0 <= evidence.quote_start_offset < evidence.quote_end_offset <= len(chunk.text)
            or chunk.text[evidence.quote_start_offset:evidence.quote_end_offset] != evidence.quotation_text
            or chunk.end_offset - chunk.start_offset != len(chunk.text)):
            raise ValueError('Citation provenance is invalid.')
        entries.append([evidence.id, doc.id, doc.paper_id, doc.content_hash, chunk.id, chunk.page_number,
                        chunk.start_offset, chunk.end_offset, sha256(chunk.text.encode()).hexdigest(),
                        evidence.quote_start_offset, evidence.quote_end_offset, evidence.quotation_text])
    value=[claim.id, claim.paper_id, claim.claim_text, claim.claim_type.value,
           claim.uncertainty_or_missing_information, sorted(entries)]
    return sha256(json.dumps(value, ensure_ascii=True, separators=(',',':')).encode()).hexdigest()


def review_envelope(claim, body):
    """Build a review envelope after a human explicitly supplies an assessment."""
    return {'method':'human', 'reviewer':body.reviewer, 'rationale':body.rationale,
            'status':body.semantic_status.value, 'reviewed_at':datetime.now(timezone.utc).isoformat(),
            'evidence_snapshot':evidence_snapshot(claim)}


def current_human_review(claim):
    verification=claim.verification_result
    if verification is None or verification.verifier_version != VERSION: return None
    try:
        envelope=json.loads(verification.rationale)
        if (not isinstance(envelope,dict) or envelope.get('method') != 'human'
            or not isinstance(envelope.get('reviewer'),str) or not envelope['reviewer'].strip()
            or not isinstance(envelope.get('rationale'),str) or not envelope['rationale'].strip()
            or not isinstance(envelope.get('reviewed_at'),str)
            or envelope.get('status') != verification.semantic_status.value
            or envelope.get('evidence_snapshot') != evidence_snapshot(claim)):
            return None
        return envelope
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


def record_human_review(session, claim, body):
    if not body.reviewed_evidence:
        raise HTTPException(403, 'Explicit review of the evidence is required.')
    if claim.verification_result is not None:
        raise HTTPException(409, 'A review already exists; revision history is not yet supported.')
    try:
        envelope=review_envelope(claim,body)
        for link in claim.evidence_links:
            ensure_processed_source(link.evidence_chunk, link.evidence_chunk.source_document)
    except ValueError:
        raise HTTPException(409, 'Claim citation provenance is invalid.') from None
    verification=VerificationResult(claim=claim,semantic_status=body.semantic_status,
        rationale=json.dumps(envelope,ensure_ascii=True,separators=(',',':')),verifier_version=VERSION)
    session.add(verification); session.flush()
    return envelope

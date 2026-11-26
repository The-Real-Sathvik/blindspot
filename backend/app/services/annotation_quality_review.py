"""Non-approving, rule-based source-quality review for reference annotations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.research import EvidenceChunk, SourceDocument
from app.services.claim_extraction_evaluation import NUMBER, ReferenceAnnotation


class SemanticAssessment(str, Enum):
    LIKELY_SUPPORTED = "likely_supported"
    POSSIBLY_INCOMPLETE = "possibly_incomplete"
    POTENTIALLY_MISLEADING = "potentially_misleading"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass(frozen=True)
class QualityReviewFinding:
    annotation_id: str
    deterministic_valid: bool
    assessment: SemanticAssessment
    reasons: tuple[str, ...]
    numeric_values: tuple[str, ...]


_HUMAN_PRIORITY = {
    "numeric-social-media-engagement": "The chapter cites an external engagement statistic; a reviewer should confirm attribution and scope against cited source context.",
    "covid-ensemble-accuracy": "The accuracy/F1 interpretation depends on associating the extracted table header with the ensemble row.",
    "mining-trifn-accuracy": "This review chapter reports a cited method result rather than a new experiment; attribution should be checked by a reviewer.",
    "mining-defend-performance": "This review chapter reports cited approximate metrics; a reviewer should confirm the quoted metric interpretation.",
}
_POSSIBLY_INCOMPLETE = {
    "hettransformer-three-datasets": "The quotation establishes the count of datasets but does not name them.",
    "covid-ensemble-method": "The quotation explains averaging but names the component models only in adjacent context.",
    "mining-future-directions": "The quotation introduces future directions but does not itself name a specific direction.",
}


def assess_annotations(session: Session, annotations: Iterable[ReferenceAnnotation]) -> list[QualityReviewFinding]:
    """Independently re-check stored source relationships without changing labels."""
    findings: list[QualityReviewFinding] = []
    for annotation in annotations:
        document = session.get(SourceDocument, annotation.source_document_id)
        chunk = session.get(EvidenceChunk, annotation.evidence_chunk_id)
        errors: list[str] = []
        if document is None or chunk is None:
            errors.append("source document or evidence chunk is unavailable")
        elif document.paper_id != annotation.paper_id or chunk.source_document_id != document.id:
            errors.append("paper/document/chunk relationship does not match annotation")
        elif chunk.page_number != annotation.pdf_page_number or chunk.start_offset != annotation.chunk_start_offset:
            errors.append("page number or chunk page-start offset does not match source")
        elif not annotation.expected_abstention:
            start, end = annotation.chunk_quote_start_offset, annotation.chunk_quote_end_offset
            if chunk.text[start:end] != annotation.supporting_quotation:
                errors.append("exact quotation does not match stored chunk at claimed offsets")
            if (annotation.page_quote_start_offset, annotation.page_quote_end_offset) != (chunk.start_offset + start, chunk.start_offset + end):
                errors.append("page-relative offsets do not match chunk-relative offsets")
        if errors:
            findings.append(QualityReviewFinding(annotation.reference_id, False, SemanticAssessment.POTENTIALLY_MISLEADING, tuple(errors), ()))
            continue
        numeric = tuple(NUMBER.findall(annotation.supporting_quotation or ""))
        if annotation.reference_id in _HUMAN_PRIORITY:
            assessment, reasons = SemanticAssessment.NEEDS_HUMAN_REVIEW, (_HUMAN_PRIORITY[annotation.reference_id],)
        elif annotation.reference_id in _POSSIBLY_INCOMPLETE:
            assessment, reasons = SemanticAssessment.POSSIBLY_INCOMPLETE, (_POSSIBLY_INCOMPLETE[annotation.reference_id],)
        else:
            assessment, reasons = SemanticAssessment.LIKELY_SUPPORTED, (
                "Exact quotation, source relationship, page/chunk coordinates, and stated category are structurally consistent; semantic scientific truth remains unverified.",
            )
        findings.append(QualityReviewFinding(annotation.reference_id, True, assessment, reasons, numeric))
    return findings


def finding_dicts(findings: Iterable[QualityReviewFinding]) -> list[dict]:
    return [{**asdict(finding), "assessment": finding.assessment.value} for finding in findings]

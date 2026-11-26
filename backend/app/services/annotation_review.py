"""Human-review package support for claim-extraction reference annotations.

Review decisions live separately from the source-grounded annotation dataset so
no automated process can silently mark scientific labels as approved.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.research import EvidenceChunk
from app.services.claim_extraction_evaluation import (
    AnnotationReviewStatus,
    DatasetSplit,
    ReferenceAnnotation,
)


class ReviewDecision(str, Enum):
    APPROVED = "approved"
    NEEDS_CORRECTION = "needs_correction"
    REJECTED = "rejected"
    NOT_YET_REVIEWED = "not_yet_reviewed"


class AnnotationReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotation_id: str = Field(min_length=1)
    decision: ReviewDecision = ReviewDecision.NOT_YET_REVIEWED
    reviewer_name: str | None = None
    reviewed_at: datetime | None = None
    reviewer_notes: str | None = None
    correction_reason: str | None = None

    @model_validator(mode="after")
    def require_explicit_human_review_details(self) -> "AnnotationReviewRecord":
        if self.decision is ReviewDecision.NOT_YET_REVIEWED:
            if any((self.reviewer_name, self.reviewed_at, self.reviewer_notes, self.correction_reason)):
                raise ValueError("not_yet_reviewed records must not contain review details")
        else:
            if not self.reviewer_name or self.reviewed_at is None:
                raise ValueError("a review decision requires reviewer_name and reviewed_at")
            if self.decision in {ReviewDecision.NEEDS_CORRECTION, ReviewDecision.REJECTED} and not self.reviewer_notes:
                raise ValueError("correction or rejection requires reviewer_notes")
            if self.decision is ReviewDecision.NEEDS_CORRECTION and not self.correction_reason:
                raise ValueError("needs_correction requires correction_reason")
        return self


class AnnotationReviewPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str = Field(min_length=1)
    annotation_dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: list[AnnotationReviewRecord]


@dataclass(frozen=True)
class ReviewStatusCounts:
    approved: int
    needs_correction: int
    rejected: int
    not_yet_reviewed: int


def annotation_dataset_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def new_review_package(annotations: Iterable[ReferenceAnnotation], *, dataset_version: str, dataset_hash: str) -> AnnotationReviewPackage:
    return AnnotationReviewPackage(
        dataset_version=dataset_version,
        annotation_dataset_sha256=dataset_hash,
        records=[AnnotationReviewRecord(annotation_id=annotation.reference_id) for annotation in annotations],
    )


def validate_review_package(package: AnnotationReviewPackage, annotations: Iterable[ReferenceAnnotation], *, dataset_version: str, dataset_hash: str) -> None:
    annotations = list(annotations)
    if package.dataset_version != dataset_version or package.annotation_dataset_sha256 != dataset_hash:
        raise ValueError("review package does not match the annotation dataset version")
    record_ids = [record.annotation_id for record in package.records]
    expected_ids = [annotation.reference_id for annotation in annotations]
    if len(record_ids) != len(set(record_ids)) or set(record_ids) != set(expected_ids):
        raise ValueError("review package must contain exactly one record for every annotation identity")


def review_status_counts(package: AnnotationReviewPackage) -> ReviewStatusCounts:
    decisions = [record.decision for record in package.records]
    return ReviewStatusCounts(*(decisions.count(value) for value in ReviewDecision))


def annotations_after_review(annotations: Iterable[ReferenceAnnotation], package: AnnotationReviewPackage) -> list[ReferenceAnnotation]:
    """Return only explicit human-approved references; rejected/draft items stay out."""
    decisions = {record.annotation_id: record.decision for record in package.records}
    return [annotation.model_copy(update={"review_status": AnnotationReviewStatus.HUMAN_REVIEWED})
            for annotation in annotations if decisions[annotation.reference_id] is ReviewDecision.APPROVED]


def require_development_only(annotations: Iterable[ReferenceAnnotation]) -> list[ReferenceAnnotation]:
    annotations = list(annotations)
    if any(annotation.dataset_split is DatasetSplit.HELD_OUT for annotation in annotations):
        raise ValueError("held_out annotations must not be used for development or prompt tuning")
    return annotations


def render_review_markdown(session: Session, annotations: Iterable[ReferenceAnnotation], package: AnnotationReviewPackage, *, dataset_version: str) -> str:
    records = {record.annotation_id: record for record in package.records}
    lines = [
        "# Blindspot scientific claim annotation review package",
        "",
        f"Dataset version: `{dataset_version}`",
        "",
        "All entries are **not yet human-reviewed** unless a named reviewer records an explicit decision in the companion JSON file. Provenance validation does not approve a scientific label.",
        "",
        "## Recording a decision",
        "",
        "Edit only the matching record in `review_status.json`; retain annotation IDs, dataset version, and dataset hash. For `approved`, `needs_correction`, or `rejected`, supply `reviewer_name` and an ISO-8601 `reviewed_at` timestamp. Corrections and rejections also need `reviewer_notes`; corrections need `correction_reason`. Run `backend/scripts/validate_annotation_review_package.py` afterward. Do not change reference annotations or source text through this package.",
        "",
        "## Reviewer checklist",
        "",
        "1. Is the quotation genuine?",
        "2. Does it support the expected claim?",
        "3. Is the claim category correct?",
        "4. Are numbers, units, and comparisons preserved?",
        "5. Does the claim accurately represent the authors' statement?",
        "6. Are limitations explicitly stated rather than inferred?",
        "7. Is abstention appropriate for the supplied context?",
        "8. Are uncertainties accurately recorded?",
        "",
        "## Partition rule",
        "",
        "Held-out entries must never be used in prompts, prompt tuning, or model-selection iterations. If a held-out label changes, create a new dataset version and retain this package unchanged.",
    ]
    for annotation in annotations:
        chunk = session.get(EvidenceChunk, annotation.evidence_chunk_id)
        context = "[source chunk unavailable]" if chunk is None else _context_window(chunk.text, annotation)
        record = records[annotation.reference_id]
        lines.extend([
            "", f"## {annotation.reference_id}", "",
            f"- Paper: {annotation.document_title}",
            f"- Paper ID: `{annotation.paper_id}`", f"- PDF page: {annotation.pdf_page_number}",
            f"- Evidence chunk: `{annotation.evidence_chunk_id}`", f"- Partition: `{annotation.dataset_split.value}`",
            f"- Current review decision: `{record.decision.value}`", f"- Dataset annotation status: `{annotation.review_status.value}`",
            f"- Category: `{annotation.claim_category.value if annotation.claim_category else 'abstention'}`",
            f"- Expected claim: {annotation.expected_claim_meaning}",
            f"- Exact quotation: {annotation.supporting_quotation or '[abstain: no supporting quotation]'}",
            f"- Chunk offsets: `{annotation.chunk_quote_start_offset}`–`{annotation.chunk_quote_end_offset}`; chunk page-start: `{annotation.chunk_start_offset}`",
            f"- Numbers/units: {', '.join(_numbers(annotation.supporting_quotation or '')) or 'none'}",
            f"- Uncertainty: {annotation.uncertainty_or_missing_information or 'not provided'}",
            f"- Reviewer notes: {record.reviewer_notes or 'not yet reviewed'}",
            "", "Context from normalized source text:", "", f"> {context.replace(chr(10), chr(10) + '> ')}",
        ])
    return "\n".join(lines) + "\n"


def write_review_package(session: Session, annotations: Iterable[ReferenceAnnotation], *, annotation_path: Path, output_dir: Path, dataset_version: str) -> tuple[Path, Path]:
    annotations = list(annotations)
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "review_status.json"
    markdown_path = output_dir / "REVIEW_PACKET.md"
    if status_path.exists() or markdown_path.exists():
        raise FileExistsError("review package already exists; create a new versioned directory rather than overwriting reviewer decisions")
    dataset_hash = annotation_dataset_hash(annotation_path)
    package = new_review_package(annotations, dataset_version=dataset_version, dataset_hash=dataset_hash)
    status_path.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_review_markdown(session, annotations, package, dataset_version=dataset_version), encoding="utf-8")
    return markdown_path, status_path


def _context_window(text: str, annotation: ReferenceAnnotation, radius: int = 260) -> str:
    if annotation.expected_abstention:
        return text[: min(len(text), radius * 2)]
    start = max(0, (annotation.chunk_quote_start_offset or 0) - radius)
    end = min(len(text), (annotation.chunk_quote_end_offset or 0) + radius)
    return text[start:end]


def _numbers(text: str) -> list[str]:
    import re
    return re.findall(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![\w.])", text)

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.research import EvidenceChunk, Paper, SourceDocument
from app.services.annotation_review import (
    AnnotationReviewPackage,
    AnnotationReviewRecord,
    ReviewDecision,
    annotations_after_review,
    new_review_package,
    require_development_only,
    validate_review_package,
    write_review_package,
)
from app.services.claim_extraction_evaluation import (
    DatasetSplit,
    ReferenceAnnotation,
    validate_reference_provenance,
)


def source_annotation(session, *, split: DatasetSplit = DatasetSplit.DEVELOPMENT) -> ReferenceAnnotation:
    paper = Paper(title="Review source", authors=[], open_access_metadata={})
    document = SourceDocument(paper=paper, document_type="pdf")
    text = "The exact source quotation is here."
    chunk = EvidenceChunk(source_document=document, page_number=2, chunk_index=0, start_offset=10, end_offset=10 + len(text), text=text)
    session.add_all([paper, document, chunk]); session.commit()
    quote = "exact source quotation"
    start = text.index(quote)
    return ReferenceAnnotation(
        reference_id="review-case", paper_id=paper.id, document_title=paper.title,
        source_document_id=document.id, evidence_chunk_id=chunk.id, pdf_page_number=2,
        chunk_start_offset=10, expected_claim_meaning="The source contains the quoted statement.",
        claim_category="result", supporting_quotation=quote, chunk_quote_start_offset=start,
        chunk_quote_end_offset=start + len(quote), review_status="draft", dataset_split=split,
    )


def reviewed(decision: ReviewDecision, **changes) -> AnnotationReviewRecord:
    values = {"annotation_id": "review-case", "decision": decision, "reviewer_name": "Reviewer",
              "reviewed_at": datetime(2026, 11, 24, tzinfo=timezone.utc), "reviewer_notes": "Reviewed source."}
    values.update(changes)
    return AnnotationReviewRecord(**values)


def test_review_status_requires_explicit_human_details() -> None:
    with pytest.raises(ValueError, match="reviewer_name"):
        AnnotationReviewRecord(annotation_id="x", decision=ReviewDecision.APPROVED)
    with pytest.raises(ValueError, match="correction_reason"):
        reviewed(ReviewDecision.NEEDS_CORRECTION)
    assert AnnotationReviewRecord(annotation_id="x").decision is ReviewDecision.NOT_YET_REVIEWED


def test_identity_version_notes_and_rejected_exclusion(session) -> None:
    annotation = source_annotation(session)
    package = new_review_package([annotation], dataset_version="v1", dataset_hash="a" * 64)
    validate_review_package(package, [annotation], dataset_version="v1", dataset_hash="a" * 64)
    with pytest.raises(ValueError, match="version"):
        validate_review_package(package, [annotation], dataset_version="v2", dataset_hash="a" * 64)
    bad_identity = package.model_copy(update={"records": [AnnotationReviewRecord(annotation_id="other")]})
    with pytest.raises(ValueError, match="exactly one record"):
        validate_review_package(bad_identity, [annotation], dataset_version="v1", dataset_hash="a" * 64)
    rejected = AnnotationReviewPackage(dataset_version="v1", annotation_dataset_sha256="a" * 64,
        records=[reviewed(ReviewDecision.REJECTED, reviewer_notes="Reference is not suitable.")])
    assert rejected.records[0].reviewer_notes == "Reference is not suitable."
    assert annotations_after_review([annotation], rejected) == []
    approved = AnnotationReviewPackage(dataset_version="v1", annotation_dataset_sha256="a" * 64,
        records=[reviewed(ReviewDecision.APPROVED)])
    accepted = annotations_after_review([annotation], approved)
    assert len(accepted) == 1 and accepted[0].review_status.value == "human_reviewed"


def test_held_out_integrity_and_provenance_survive_review(session, tmp_path: Path) -> None:
    annotation = source_annotation(session, split=DatasetSplit.HELD_OUT)
    with pytest.raises(ValueError, match="held_out"):
        require_development_only([annotation])
    package = AnnotationReviewPackage(dataset_version="v1", annotation_dataset_sha256="b" * 64,
        records=[reviewed(ReviewDecision.APPROVED)])
    corrected = annotations_after_review([annotation], package)
    assert validate_reference_provenance(session, corrected).valid_count == 1
    annotation_path = tmp_path / "annotations.json"
    annotation_path.write_text("[]", encoding="utf-8")
    markdown, status = write_review_package(session, [annotation], annotation_path=annotation_path,
        output_dir=tmp_path / "package", dataset_version="v1")
    assert markdown.exists() and status.exists()
    assert "Held-out entries must never" in markdown.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        write_review_package(session, [annotation], annotation_path=annotation_path,
            output_dir=tmp_path / "package", dataset_version="v1")

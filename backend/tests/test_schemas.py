import pytest
from pydantic import ValidationError

from app.schemas.research import (
    DocumentStatus,
    EvidenceChunkCreate,
    ResearchJobCreate,
    SourceDocumentCreate,
)


def test_research_job_topic_is_required() -> None:
    assert ResearchJobCreate(topic="Evidence-grounded gap discovery").topic.startswith("Evidence")
    with pytest.raises(ValidationError):
        ResearchJobCreate(topic="   ")


def test_evidence_chunk_rejects_reversed_offsets() -> None:
    with pytest.raises(ValidationError, match="end_offset"):
        EvidenceChunkCreate(source_document_id="document", page_number=1, chunk_index=0, start_offset=10, end_offset=9, text="evidence")


def test_failed_document_requires_error_information() -> None:
    with pytest.raises(ValidationError, match="error_info"):
        SourceDocumentCreate(paper_id="paper", status=DocumentStatus.EXTRACTION_FAILED)

    failed = SourceDocumentCreate(paper_id="paper", status=DocumentStatus.EXTRACTION_FAILED, error_info="Encrypted PDF")
    assert failed.status is DocumentStatus.EXTRACTION_FAILED

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRIABLE_ERROR = "retriable_error"


class JobPaperStatus(str, Enum):
    DISCOVERED = "discovered"
    SCREENED = "screened"
    FULL_TEXT_UNAVAILABLE = "full_text_unavailable"
    EXTRACTION_FAILED = "extraction_failed"
    MISSING_EVIDENCE = "missing_evidence"
    ANALYSIS_FAILED = "analysis_failed"
    RETRIABLE_ERROR = "retriable_error"
    ANALYZED = "analyzed"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    EXTRACTION_FAILED = "extraction_failed"
    RETRIABLE_ERROR = "retriable_error"


class CitationCheckStatus(str, Enum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"


class SemanticSupportStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ClaimType(str, Enum):
    RESEARCH_PROBLEM = "research_problem"
    OBJECTIVE = "objective"
    METHODOLOGY = "methodology"
    DATASET = "dataset"
    RESULT = "result"
    LIMITATION = "limitation"
    FUTURE_DIRECTION = "future_direction"
    EXPLICIT_GAP = "explicit_gap"
    CONTRIBUTION = "contribution"


class SchemaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ResearchJobCreate(SchemaBase):
    topic: str = Field(min_length=3, max_length=4000)

    @field_validator("topic")
    @classmethod
    def require_nonblank_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("topic must not be blank")
        return value


class ResearchJobRead(ResearchJobCreate):
    id: str
    status: JobStatus
    error_info: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PaperCreate(SchemaBase):
    title: str = Field(min_length=1, max_length=4000)
    doi: Optional[str] = Field(default=None, max_length=512)
    openalex_id: Optional[str] = Field(default=None, max_length=512)
    arxiv_id: Optional[str] = Field(default=None, max_length=128)
    publication_year: Optional[int] = Field(default=None, ge=1600, le=2100)
    authors: list[dict] = Field(default_factory=list)
    abstract: Optional[str] = None
    venue: Optional[str] = Field(default=None, max_length=512)
    landing_page_url: Optional[HttpUrl] = None
    related_version_id: Optional[str] = None

    @field_validator("title")
    @classmethod
    def require_nonblank_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class PaperRead(PaperCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class ResearchJobPaperCreate(SchemaBase):
    paper_id: str
    status: JobPaperStatus = JobPaperStatus.DISCOVERED
    included: Optional[bool] = None
    relevance_score: Optional[float] = Field(default=None, ge=0, le=1)
    decision_reason: Optional[str] = None
    error_info: Optional[str] = None

    @model_validator(mode="after")
    def require_failure_detail(self) -> "ResearchJobPaperCreate":
        failed_states = {
            JobPaperStatus.FULL_TEXT_UNAVAILABLE,
            JobPaperStatus.EXTRACTION_FAILED,
            JobPaperStatus.MISSING_EVIDENCE,
            JobPaperStatus.ANALYSIS_FAILED,
            JobPaperStatus.RETRIABLE_ERROR,
        }
        if self.status in failed_states and not self.error_info:
            raise ValueError("error_info is required for a failed processing state")
        return self


class SourceDocumentCreate(SchemaBase):
    paper_id: str
    source_url: Optional[HttpUrl] = None
    local_path: Optional[str] = None
    document_type: str = Field(default="full_text_pdf", min_length=1, max_length=64)
    status: DocumentStatus = DocumentStatus.PENDING
    content_hash: Optional[str] = Field(default=None, max_length=128)
    error_info: Optional[str] = None

    @model_validator(mode="after")
    def require_failure_detail(self) -> "SourceDocumentCreate":
        if self.status in {DocumentStatus.UNAVAILABLE, DocumentStatus.EXTRACTION_FAILED, DocumentStatus.RETRIABLE_ERROR} and not self.error_info:
            raise ValueError("error_info is required for a failed or unavailable document")
        return self


class EvidenceChunkCreate(SchemaBase):
    source_document_id: str
    page_number: int = Field(ge=1)
    section_label: Optional[str] = Field(default=None, max_length=512)
    chunk_index: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> "EvidenceChunkCreate":
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must not precede start_offset")
        return self


class ExtractedClaimCreate(SchemaBase):
    paper_id: str
    claim_type: ClaimType
    claim_text: str = Field(min_length=1)


class ClaimEvidenceCreate(SchemaBase):
    claim_id: str
    evidence_chunk_id: str
    quotation_text: str = Field(min_length=1)
    quote_start_offset: int = Field(ge=0)
    quote_end_offset: int = Field(ge=0)
    quotation_status: CitationCheckStatus = CitationCheckStatus.UNVERIFIED
    source_location_status: CitationCheckStatus = CitationCheckStatus.UNVERIFIED

    @model_validator(mode="after")
    def validate_offsets(self) -> "ClaimEvidenceCreate":
        if self.quote_end_offset < self.quote_start_offset:
            raise ValueError("quote_end_offset must not precede quote_start_offset")
        return self


class VerificationResultCreate(SchemaBase):
    claim_id: str
    semantic_status: SemanticSupportStatus
    rationale: str = Field(min_length=1)
    verifier_version: Optional[str] = Field(default=None, max_length=128)

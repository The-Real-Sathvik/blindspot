import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.schemas.research import (
    CitationCheckStatus,
    ClaimType,
    DocumentStatus,
    JobPaperStatus,
    JobStatus,
    AgentRole,
    MatchStatus,
    ModelRunStatus,
    ExtractionOutcomeStatus,
    SemanticSupportStatus,
)


def new_id() -> str:
    return str(uuid.uuid4())


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ResearchJob(Timestamped, Base):
    __tablename__ = "research_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, native_enum=False), default=JobStatus.PENDING, nullable=False)
    error_info: Mapped[Optional[str]] = mapped_column(Text)
    papers: Mapped[list["ResearchJobPaper"]] = relationship(back_populates="research_job", cascade="all, delete-orphan")
    search_runs: Mapped[list["SearchRun"]] = relationship(back_populates="research_job", cascade="all, delete-orphan")
    model_runs: Mapped[list["ModelRun"]] = relationship(back_populates="research_job")


class Paper(Timestamped, Base):
    __tablename__ = "papers"
    __table_args__ = (
        UniqueConstraint("doi", name="uq_papers_doi"),
        UniqueConstraint("openalex_id", name="uq_papers_openalex_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    doi: Mapped[Optional[str]] = mapped_column(String(512))
    openalex_id: Mapped[Optional[str]] = mapped_column(String(512))
    arxiv_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    publication_year: Mapped[Optional[int]] = mapped_column(Integer)
    authors: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    abstract: Mapped[Optional[str]] = mapped_column(Text)
    venue: Mapped[Optional[str]] = mapped_column(String(512))
    landing_page_url: Mapped[Optional[str]] = mapped_column(Text)
    open_access_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    publication_type: Mapped[Optional[str]] = mapped_column(String(128))
    publication_type_source: Mapped[Optional[str]] = mapped_column(String(64))
    related_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    related_version: Mapped[Optional["Paper"]] = relationship(remote_side="Paper.id", back_populates="version_children")
    version_children: Mapped[list["Paper"]] = relationship(back_populates="related_version")
    research_jobs: Mapped[list["ResearchJobPaper"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    source_documents: Mapped[list["SourceDocument"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    extracted_claims: Mapped[list["ExtractedClaim"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    external_identifiers: Mapped[list["PaperExternalIdentifier"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class ResearchJobPaper(Timestamped, Base):
    __tablename__ = "research_job_papers"
    __table_args__ = (UniqueConstraint("research_job_id", "paper_id", name="uq_job_paper"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[JobPaperStatus] = mapped_column(Enum(JobPaperStatus, native_enum=False), default=JobPaperStatus.DISCOVERED, nullable=False)
    included: Mapped[Optional[bool]] = mapped_column(Boolean)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)
    error_info: Mapped[Optional[str]] = mapped_column(Text)
    research_job: Mapped["ResearchJob"] = relationship(back_populates="papers")
    paper: Mapped["Paper"] = relationship(back_populates="research_jobs")


class PaperExternalIdentifier(Timestamped, Base):
    __tablename__ = "paper_external_identifiers"
    __table_args__ = (UniqueConstraint("source_name", "identifier", name="uq_source_identifier"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str] = mapped_column(String(512), nullable=False)
    source_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
    paper: Mapped["Paper"] = relationship(back_populates="external_identifiers")


class SearchRun(Timestamped, Base):
    __tablename__ = "search_runs"
    __table_args__ = (CheckConstraint("requested_limit >= 1", name="ck_search_run_limit_positive"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_info: Mapped[Optional[str]] = mapped_column(Text)
    research_job: Mapped["ResearchJob"] = relationship(back_populates="search_runs")
    results: Mapped[list["SearchResult"]] = relationship(back_populates="search_run", cascade="all, delete-orphan")


class SearchResult(Timestamped, Base):
    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("search_run_id", "source_record_id", name="uq_search_run_record"),
        CheckConstraint("source_rank >= 1", name="ck_search_result_rank_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    search_run_id: Mapped[str] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[Optional[str]] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float)
    selection_reason: Mapped[Optional[str]] = mapped_column(Text)
    included: Mapped[Optional[bool]] = mapped_column(Boolean)
    full_text_url: Mapped[Optional[str]] = mapped_column(Text)
    full_text_available: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_abstract: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_landing_page: Mapped[Optional[bool]] = mapped_column(Boolean)
    metadata_completeness: Mapped[Optional[float]] = mapped_column(Float)
    match_status: Mapped[Optional[MatchStatus]] = mapped_column(Enum(MatchStatus, native_enum=False, values_callable=lambda enum: [item.value for item in enum]), nullable=True)
    match_reason: Mapped[Optional[str]] = mapped_column(Text)
    search_run: Mapped["SearchRun"] = relationship(back_populates="results")
    paper: Mapped[Optional["Paper"]] = relationship()


class ModelRun(Timestamped, Base):
    __tablename__ = "model_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("research_jobs.id", ondelete="SET NULL"))
    agent_role: Mapped[AgentRole] = mapped_column(Enum(AgentRole, native_enum=False, values_callable=lambda enum: [item.value for item in enum]), nullable=False)
    model_tag: Mapped[str] = mapped_column(String(256), nullable=False)
    model_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_reference_hash: Mapped[Optional[str]] = mapped_column(String(128))
    output_hash: Mapped[Optional[str]] = mapped_column(String(128))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    runtime_seconds: Mapped[Optional[float]] = mapped_column(Float)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_per_second: Mapped[Optional[float]] = mapped_column(Float)
    available_memory_before_mb: Mapped[Optional[float]] = mapped_column(Float)
    available_memory_minimum_mb: Mapped[Optional[float]] = mapped_column(Float)
    available_memory_after_mb: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[ModelRunStatus] = mapped_column(Enum(ModelRunStatus, native_enum=False, values_callable=lambda enum: [item.value for item in enum]), nullable=False)
    outcome_status: Mapped[Optional[ExtractionOutcomeStatus]] = mapped_column(Enum(ExtractionOutcomeStatus, native_enum=False, values_callable=lambda enum: [item.value for item in enum]), nullable=True)
    error_category: Mapped[Optional[str]] = mapped_column(String(64))
    error_summary: Mapped[Optional[str]] = mapped_column(String(512))
    research_job: Mapped[Optional["ResearchJob"]] = relationship(back_populates="model_runs")
    extracted_claims: Mapped[list["ExtractedClaim"]] = relationship(back_populates="model_run")


class SourceDocument(Timestamped, Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    local_path: Mapped[Optional[str]] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(64), default="full_text_pdf", nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus, native_enum=False), default=DocumentStatus.PENDING, nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(128))
    error_info: Mapped[Optional[str]] = mapped_column(Text)
    paper: Mapped["Paper"] = relationship(back_populates="source_documents")
    evidence_chunks: Mapped[list["EvidenceChunk"]] = relationship(back_populates="source_document", cascade="all, delete-orphan")
    identity_reviews: Mapped[list["UploadIdentityReview"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentProcessingAttempt(Base):
    __tablename__ = 'document_processing_attempts'
    __table_args__ = (
        CheckConstraint("operation IN ('acquire', 'extract')", name='ck_document_attempt_operation'),
        CheckConstraint("outcome IN ('started', 'succeeded', 'failed')", name='ck_document_attempt_outcome'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_job_id: Mapped[str] = mapped_column(ForeignKey('research_jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey('papers.id', ondelete='CASCADE'), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    retryable: Mapped[Optional[bool]] = mapped_column(Boolean)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class UploadIdentityReview(Base):
    __tablename__ = "upload_identity_reviews"
    __table_args__ = (CheckConstraint("outcome = 'human_confirmed'", name="ck_upload_review_outcome"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id", ondelete="RESTRICT"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_action: Mapped[str] = mapped_column(String(64), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison: Mapped[dict] = mapped_column(JSON, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document: Mapped["SourceDocument"] = relationship(back_populates="identity_reviews")


class EvidenceChunk(Timestamped, Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (
        UniqueConstraint("source_document_id", "chunk_index", name="uq_document_chunk_index"),
        CheckConstraint("page_number >= 1", name="ck_chunk_page_positive"),
        CheckConstraint("start_offset >= 0", name="ck_chunk_start_nonnegative"),
        CheckConstraint("end_offset >= start_offset", name="ck_chunk_offsets_ordered"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section_label: Mapped[Optional[str]] = mapped_column(String(512))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_document: Mapped["SourceDocument"] = relationship(back_populates="evidence_chunks")
    claim_evidence: Mapped[list["ClaimEvidence"]] = relationship(back_populates="evidence_chunk")


class ExtractedClaim(Timestamped, Base):
    __tablename__ = "extracted_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    model_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("model_runs.id", ondelete="SET NULL"))
    claim_type: Mapped[ClaimType] = mapped_column(Enum(ClaimType, native_enum=False), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty_or_missing_information: Mapped[Optional[str]] = mapped_column(Text)
    paper: Mapped["Paper"] = relationship(back_populates="extracted_claims")
    evidence_links: Mapped[list["ClaimEvidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    verification_result: Mapped[Optional["VerificationResult"]] = relationship(back_populates="claim", cascade="all, delete-orphan", uselist=False)
    model_run: Mapped[Optional["ModelRun"]] = relationship(back_populates="extracted_claims")


class ClaimEvidence(Timestamped, Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        CheckConstraint("quote_start_offset >= 0", name="ck_quote_start_nonnegative"),
        CheckConstraint("quote_end_offset >= quote_start_offset", name="ck_quote_offsets_ordered"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("extracted_claims.id", ondelete="CASCADE"), nullable=False)
    evidence_chunk_id: Mapped[str] = mapped_column(ForeignKey("evidence_chunks.id", ondelete="RESTRICT"), nullable=False)
    quotation_text: Mapped[str] = mapped_column(Text, nullable=False)
    quote_start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    quotation_status: Mapped[CitationCheckStatus] = mapped_column(Enum(CitationCheckStatus, native_enum=False), default=CitationCheckStatus.UNVERIFIED, nullable=False)
    source_location_status: Mapped[CitationCheckStatus] = mapped_column(Enum(CitationCheckStatus, native_enum=False), default=CitationCheckStatus.UNVERIFIED, nullable=False)
    claim: Mapped["ExtractedClaim"] = relationship(back_populates="evidence_links")
    evidence_chunk: Mapped["EvidenceChunk"] = relationship(back_populates="claim_evidence")


class VerificationResult(Timestamped, Base):
    __tablename__ = "verification_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("extracted_claims.id", ondelete="CASCADE"), nullable=False, unique=True)
    semantic_status: Mapped[SemanticSupportStatus] = mapped_column(Enum(SemanticSupportStatus, native_enum=False), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    verifier_version: Mapped[Optional[str]] = mapped_column(String(128))
    claim: Mapped["ExtractedClaim"] = relationship(back_populates="verification_result")


class CandidateGap(Timestamped, Base):
    __tablename__ = "candidate_gaps"
    __table_args__ = (CheckConstraint("validation_status = 'not_yet_validated'", name="ck_gap_validation_status"), CheckConstraint("category IN ('conflicting_findings', 'missing_evaluation', 'methodological_limitation', 'dataset_generalization_concern', 'underexplored_comparison')", name="ck_gap_category"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False)
    model_run_id: Mapped[str] = mapped_column(ForeignKey("model_runs.id", ondelete="RESTRICT"), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    counterevidence: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_yet_validated")
    references: Mapped[list["CandidateGapReference"]] = relationship(cascade="all, delete-orphan", back_populates="gap")


class CandidateGapReference(Base):
    __tablename__ = "candidate_gap_references"
    __table_args__ = (UniqueConstraint("gap_id", "claim_evidence_id", name="uq_gap_evidence"), CheckConstraint("role IN ('supporting', 'counterevidence')", name="ck_gap_reference_role"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    gap_id: Mapped[str] = mapped_column(ForeignKey("candidate_gaps.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False)
    claim_id: Mapped[str] = mapped_column(ForeignKey("extracted_claims.id", ondelete="RESTRICT"), nullable=False)
    claim_evidence_id: Mapped[str] = mapped_column(ForeignKey("claim_evidence.id", ondelete="RESTRICT"), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    gap: Mapped["CandidateGap"] = relationship(back_populates="references")

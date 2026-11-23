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
    related_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    related_version: Mapped[Optional["Paper"]] = relationship(remote_side="Paper.id", back_populates="version_children")
    version_children: Mapped[list["Paper"]] = relationship(back_populates="related_version")
    research_jobs: Mapped[list["ResearchJobPaper"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    source_documents: Mapped[list["SourceDocument"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    extracted_claims: Mapped[list["ExtractedClaim"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


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
    claim_type: Mapped[ClaimType] = mapped_column(Enum(ClaimType, native_enum=False), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    paper: Mapped["Paper"] = relationship(back_populates="extracted_claims")
    evidence_links: Mapped[list["ClaimEvidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    verification_result: Mapped[Optional["VerificationResult"]] = relationship(back_populates="claim", cascade="all, delete-orphan", uselist=False)


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

"""initial provenance model

Revision ID: 20261123_0001
Revises:
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa


revision = "20261123_0001"
down_revision = None
branch_labels = None
depends_on = None

job_status = sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", "RETRIABLE_ERROR", name="jobstatus", native_enum=False)
job_paper_status = sa.Enum("DISCOVERED", "SCREENED", "FULL_TEXT_UNAVAILABLE", "EXTRACTION_FAILED", "MISSING_EVIDENCE", "ANALYSIS_FAILED", "RETRIABLE_ERROR", "ANALYZED", name="jobpaperstatus", native_enum=False)
document_status = sa.Enum("PENDING", "AVAILABLE", "UNAVAILABLE", "EXTRACTION_FAILED", "RETRIABLE_ERROR", name="documentstatus", native_enum=False)
claim_type = sa.Enum("RESEARCH_PROBLEM", "OBJECTIVE", "METHODOLOGY", "DATASET", "RESULT", "LIMITATION", "FUTURE_DIRECTION", "EXPLICIT_GAP", "CONTRIBUTION", name="claimtype", native_enum=False)
citation_check_status = sa.Enum("UNVERIFIED", "VALID", "INVALID", name="citationcheckstatus", native_enum=False)
semantic_support_status = sa.Enum("SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE", name="semanticsupportstatus", native_enum=False)


def upgrade() -> None:
    op.create_table("research_jobs", sa.Column("id", sa.String(36), primary_key=True), sa.Column("topic", sa.Text(), nullable=False), sa.Column("status", job_status, nullable=False), sa.Column("error_info", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("papers", sa.Column("id", sa.String(36), primary_key=True), sa.Column("title", sa.Text(), nullable=False), sa.Column("doi", sa.String(512)), sa.Column("openalex_id", sa.String(512)), sa.Column("arxiv_id", sa.String(128)), sa.Column("publication_year", sa.Integer()), sa.Column("authors", sa.JSON(), nullable=False), sa.Column("abstract", sa.Text()), sa.Column("venue", sa.String(512)), sa.Column("landing_page_url", sa.Text()), sa.Column("related_version_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("doi", name="uq_papers_doi"), sa.UniqueConstraint("openalex_id", name="uq_papers_openalex_id"))
    op.create_index("ix_papers_arxiv_id", "papers", ["arxiv_id"])
    op.create_table("research_job_papers", sa.Column("id", sa.String(36), primary_key=True), sa.Column("research_job_id", sa.String(36), sa.ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False), sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False), sa.Column("status", job_paper_status, nullable=False), sa.Column("included", sa.Boolean()), sa.Column("relevance_score", sa.Float()), sa.Column("decision_reason", sa.Text()), sa.Column("error_info", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("research_job_id", "paper_id", name="uq_job_paper"))
    op.create_table("source_documents", sa.Column("id", sa.String(36), primary_key=True), sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False), sa.Column("source_url", sa.Text()), sa.Column("local_path", sa.Text()), sa.Column("document_type", sa.String(64), nullable=False), sa.Column("status", document_status, nullable=False), sa.Column("content_hash", sa.String(128)), sa.Column("error_info", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("evidence_chunks", sa.Column("id", sa.String(36), primary_key=True), sa.Column("source_document_id", sa.String(36), sa.ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False), sa.Column("page_number", sa.Integer(), nullable=False), sa.Column("section_label", sa.String(512)), sa.Column("chunk_index", sa.Integer(), nullable=False), sa.Column("start_offset", sa.Integer(), nullable=False), sa.Column("end_offset", sa.Integer(), nullable=False), sa.Column("text", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("page_number >= 1", name="ck_chunk_page_positive"), sa.CheckConstraint("start_offset >= 0", name="ck_chunk_start_nonnegative"), sa.CheckConstraint("end_offset >= start_offset", name="ck_chunk_offsets_ordered"), sa.UniqueConstraint("source_document_id", "chunk_index", name="uq_document_chunk_index"))
    op.create_table("extracted_claims", sa.Column("id", sa.String(36), primary_key=True), sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False), sa.Column("claim_type", claim_type, nullable=False), sa.Column("claim_text", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("claim_evidence", sa.Column("id", sa.String(36), primary_key=True), sa.Column("claim_id", sa.String(36), sa.ForeignKey("extracted_claims.id", ondelete="CASCADE"), nullable=False), sa.Column("evidence_chunk_id", sa.String(36), sa.ForeignKey("evidence_chunks.id", ondelete="RESTRICT"), nullable=False), sa.Column("quotation_text", sa.Text(), nullable=False), sa.Column("quote_start_offset", sa.Integer(), nullable=False), sa.Column("quote_end_offset", sa.Integer(), nullable=False), sa.Column("quotation_status", citation_check_status, nullable=False), sa.Column("source_location_status", citation_check_status, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.CheckConstraint("quote_start_offset >= 0", name="ck_quote_start_nonnegative"), sa.CheckConstraint("quote_end_offset >= quote_start_offset", name="ck_quote_offsets_ordered"))
    op.create_table("verification_results", sa.Column("id", sa.String(36), primary_key=True), sa.Column("claim_id", sa.String(36), sa.ForeignKey("extracted_claims.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("semantic_status", semantic_support_status, nullable=False), sa.Column("rationale", sa.Text(), nullable=False), sa.Column("verifier_version", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade() -> None:
    op.drop_table("verification_results")
    op.drop_table("claim_evidence")
    op.drop_table("extracted_claims")
    op.drop_table("evidence_chunks")
    op.drop_table("source_documents")
    op.drop_table("research_job_papers")
    op.drop_index("ix_papers_arxiv_id", table_name="papers")
    op.drop_table("papers")
    op.drop_table("research_jobs")

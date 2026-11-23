"""add source-independent search provenance

Revision ID: 20261123_0003
Revises: 20261123_0002
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "20261123_0003"
down_revision = "20261123_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_external_identifiers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("identifier", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_name", "identifier", name="uq_source_identifier"),
    )
    op.create_table(
        "search_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("research_job_id", sa.String(36), sa.ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("retrieved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_info", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("requested_limit >= 1", name="ck_search_run_limit_positive"),
    )
    op.create_table(
        "search_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("search_run_id", sa.String(36), sa.ForeignKey("search_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="SET NULL")),
        sa.Column("source_record_id", sa.String(512), nullable=False),
        sa.Column("source_rank", sa.Integer(), nullable=False),
        sa.Column("relevance_score", sa.Float()),
        sa.Column("selection_reason", sa.Text()),
        sa.Column("included", sa.Boolean()),
        sa.Column("full_text_url", sa.Text()),
        sa.Column("full_text_available", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("search_run_id", "source_record_id", name="uq_search_run_record"),
        sa.CheckConstraint("source_rank >= 1", name="ck_search_result_rank_positive"),
    )


def downgrade() -> None:
    op.drop_table("search_results")
    op.drop_table("search_runs")
    op.drop_table("paper_external_identifiers")

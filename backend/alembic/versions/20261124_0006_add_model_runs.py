"""add model run provenance

Revision ID: 20261124_0006
Revises: 20261123_0005
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = "20261124_0006"
down_revision = "20261123_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("research_job_id", sa.String(36), sa.ForeignKey("research_jobs.id", ondelete="SET NULL")),
        sa.Column("agent_role", sa.String(64), nullable=False),
        sa.Column("model_tag", sa.String(256), nullable=False),
        sa.Column("model_config", sa.JSON(), nullable=False),
        sa.Column("prompt_template_version", sa.String(64), nullable=False),
        sa.Column("input_reference_hash", sa.String(128)),
        sa.Column("output_hash", sa.String(128)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("runtime_seconds", sa.Float()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("tokens_per_second", sa.Float()),
        sa.Column("available_memory_before_mb", sa.Float()),
        sa.Column("available_memory_minimum_mb", sa.Float()),
        sa.Column("available_memory_after_mb", sa.Float()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_summary", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("agent_role IN ('claim_extraction', 'gap_detection', 'semantic_review')", name="ck_model_run_agent_role"),
        sa.CheckConstraint("status IN ('success', 'failed')", name="ck_model_run_status"),
    )


def downgrade() -> None:
    op.drop_table("model_runs")

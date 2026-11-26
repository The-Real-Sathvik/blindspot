"""Add provisional candidate gaps and traceable references.

Revision ID: 20261125_0008
Revises: 20261124_0007
"""
from alembic import op
import sqlalchemy as sa

revision = "20261125_0008"
down_revision = "20261124_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("candidate_gaps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("research_job_id", sa.String(36), sa.ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_run_id", sa.String(36), sa.ForeignKey("model_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False), sa.Column("category", sa.String(64), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False), sa.Column("counterevidence", sa.Text(), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=False), sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("validation_status = 'not_yet_validated'", name="ck_gap_validation_status"),
        sa.CheckConstraint("category IN ('conflicting_findings', 'missing_evaluation', 'methodological_limitation', 'dataset_generalization_concern', 'underexplored_comparison')", name="ck_gap_category"))
    op.create_table("candidate_gap_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("gap_id", sa.String(36), sa.ForeignKey("candidate_gaps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("extracted_claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("claim_evidence_id", sa.String(36), sa.ForeignKey("claim_evidence.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.UniqueConstraint("gap_id", "claim_evidence_id", name="uq_gap_evidence"),
        sa.CheckConstraint("role IN ('supporting', 'counterevidence')", name="ck_gap_reference_role"))


def downgrade():
    op.drop_table("candidate_gap_references")
    op.drop_table("candidate_gaps")

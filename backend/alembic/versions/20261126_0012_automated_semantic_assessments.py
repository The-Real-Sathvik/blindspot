"""Persist automated semantic assessments separately from human reviews."""
from alembic import op
import sqlalchemy as sa

revision = "20261126_0012"
down_revision = "20261126_0011"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("automated_semantic_assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("extracted_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_run_id", sa.String(36), sa.ForeignKey("model_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_snapshot", sa.String(64), nullable=False),
        sa.Column("assessment_status", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False), sa.Column("uncertainty", sa.Text()),
        sa.Column("assessment_version", sa.String(64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("claim_id", "evidence_snapshot", "assessment_version", name="uq_automated_assessment_identity"),
        sa.CheckConstraint("assessment_status IN ('SUPPORTED', 'PARTIALLY_SUPPORTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE')", name="ck_automated_assessment_status"))
    op.create_index("ix_automated_semantic_assessments_claim_id", "automated_semantic_assessments", ["claim_id"])

def downgrade():
    op.drop_index("ix_automated_semantic_assessments_claim_id", table_name="automated_semantic_assessments")
    op.drop_table("automated_semantic_assessments")

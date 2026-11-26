"""Add separately-provenanced automated provisional gaps."""
from alembic import op
import sqlalchemy as sa
revision = "20261126_0013"
down_revision = "20261126_0012"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("automated_provisional_gaps", sa.Column("id",sa.String(36),primary_key=True),sa.Column("research_job_id",sa.String(36),sa.ForeignKey("research_jobs.id",ondelete="CASCADE"),nullable=False),sa.Column("model_run_id",sa.String(36),sa.ForeignKey("model_runs.id",ondelete="RESTRICT"),nullable=False),sa.Column("identity_hash",sa.String(64),nullable=False),sa.Column("statement",sa.Text(),nullable=False),sa.Column("rationale",sa.Text(),nullable=False),sa.Column("uncertainty",sa.Text(),nullable=False),sa.Column("validation_status",sa.String(64),nullable=False),sa.UniqueConstraint("research_job_id","identity_hash",name="uq_automated_provisional_gap_identity"),sa.CheckConstraint("validation_status = 'provisional_not_globally_validated'",name="ck_automated_provisional_gap_status"))
    op.create_index("ix_automated_provisional_gaps_research_job_id","automated_provisional_gaps",["research_job_id"])
    op.create_table("automated_provisional_gap_references",sa.Column("id",sa.String(36),primary_key=True),sa.Column("gap_id",sa.String(36),sa.ForeignKey("automated_provisional_gaps.id",ondelete="CASCADE"),nullable=False),sa.Column("paper_id",sa.String(36),sa.ForeignKey("papers.id",ondelete="RESTRICT"),nullable=False),sa.Column("claim_id",sa.String(36),sa.ForeignKey("extracted_claims.id",ondelete="RESTRICT"),nullable=False),sa.Column("automated_assessment_id",sa.String(36),sa.ForeignKey("automated_semantic_assessments.id",ondelete="RESTRICT"),nullable=False),sa.Column("claim_evidence_id",sa.String(36),sa.ForeignKey("claim_evidence.id",ondelete="RESTRICT"),nullable=False),sa.Column("role",sa.String(24),nullable=False),sa.UniqueConstraint("gap_id","automated_assessment_id",name="uq_automated_provisional_gap_assessment"),sa.CheckConstraint("role IN ('supporting', 'counterevidence')",name="ck_automated_provisional_gap_reference_role"))
def downgrade():
    op.drop_table("automated_provisional_gap_references"); op.drop_index("ix_automated_provisional_gaps_research_job_id",table_name="automated_provisional_gaps"); op.drop_table("automated_provisional_gaps")

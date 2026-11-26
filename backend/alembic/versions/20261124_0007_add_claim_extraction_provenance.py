"""add claim extraction provenance

Revision ID: 20261124_0007
Revises: 20261124_0006
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = "20261124_0007"
down_revision = "20261124_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("extracted_claims") as batch_op:
        batch_op.add_column(sa.Column("model_run_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key("fk_extracted_claim_model_run", "model_runs", ["model_run_id"], ["id"], ondelete="SET NULL")
        batch_op.add_column(sa.Column("uncertainty_or_missing_information", sa.Text(), nullable=True))
    with op.batch_alter_table("model_runs") as batch_op:
        batch_op.add_column(sa.Column("outcome_status", sa.String(32), nullable=True))
        batch_op.create_check_constraint("ck_model_run_outcome_status", "outcome_status IS NULL OR outcome_status IN ('claims_extracted', 'no_claims', 'partial_error', 'failed_inference')")


def downgrade() -> None:
    with op.batch_alter_table("model_runs") as batch_op:
        batch_op.drop_constraint("ck_model_run_outcome_status", type_="check")
        batch_op.drop_column("outcome_status")
    with op.batch_alter_table("extracted_claims") as batch_op:
        batch_op.drop_column("uncertainty_or_missing_information")
        batch_op.drop_constraint("fk_extracted_claim_model_run", type_="foreignkey")
        batch_op.drop_column("model_run_id")

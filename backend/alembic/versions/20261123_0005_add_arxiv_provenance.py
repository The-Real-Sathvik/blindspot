"""add arxiv source metadata and match provenance

Revision ID: 20261123_0005
Revises: 20261123_0004
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "20261123_0005"
down_revision = "20261123_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("paper_external_identifiers") as batch_op:
        batch_op.add_column(sa.Column("source_metadata", sa.JSON(), nullable=True))
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.add_column(sa.Column("match_status", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("match_reason", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_search_result_match_status",
            "match_status IS NULL OR match_status IN ('new', 'matched_doi', 'matched_arxiv_id', 'matched_title_author', 'ambiguous', 'unresolved', 'not_checked')",
        )


def downgrade() -> None:
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.drop_constraint("ck_search_result_match_status", type_="check")
        batch_op.drop_column("match_reason")
        batch_op.drop_column("match_status")
    with op.batch_alter_table("paper_external_identifiers") as batch_op:
        batch_op.drop_column("source_metadata")

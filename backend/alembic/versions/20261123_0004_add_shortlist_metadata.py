"""add shortlist metadata fields

Revision ID: 20261123_0004
Revises: 20261123_0003
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "20261123_0004"
down_revision = "20261123_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.add_column(sa.Column("publication_type", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("publication_type_source", sa.String(64), nullable=True))
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.add_column(sa.Column("has_abstract", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("has_landing_page", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("metadata_completeness", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.drop_column("metadata_completeness")
        batch_op.drop_column("has_landing_page")
        batch_op.drop_column("has_abstract")
    with op.batch_alter_table("papers") as batch_op:
        batch_op.drop_column("publication_type_source")
        batch_op.drop_column("publication_type")

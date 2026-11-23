"""add OpenAlex open access metadata

Revision ID: 20261123_0002
Revises: 20261123_0001
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa


revision = "20261123_0002"
down_revision = "20261123_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.add_column(
            sa.Column("open_access_metadata", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.drop_column("open_access_metadata")

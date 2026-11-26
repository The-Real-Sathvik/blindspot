"""Repair omitted timestamp columns on automated provisional gaps."""
from alembic import op
import sqlalchemy as sa

revision = "20261126_0015"
down_revision = "20261126_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("automated_provisional_gaps", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("automated_provisional_gaps", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("automated_provisional_gaps", "updated_at")
    op.drop_column("automated_provisional_gaps", "created_at")

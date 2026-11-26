"""Add source-preserving abstract evidence separate from PDF chunks."""
from alembic import op
import sqlalchemy as sa

revision = "20261126_0011"
down_revision = "20261125_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "abstract_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(512), nullable=False),
        sa.Column("doi", sa.String(512)), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False), sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("retrieval_provenance", sa.JSON(), nullable=False),
        sa.UniqueConstraint("paper_id", "source_name", "source_record_id", name="uq_abstract_evidence_source"),
        sa.CheckConstraint("start_offset >= 0", name="ck_abstract_evidence_start_nonnegative"),
        sa.CheckConstraint("end_offset >= start_offset", name="ck_abstract_evidence_offsets_ordered"),
    )
    op.create_index("ix_abstract_evidence_paper_id", "abstract_evidence", ["paper_id"])


def downgrade():
    op.drop_index("ix_abstract_evidence_paper_id", table_name="abstract_evidence")
    op.drop_table("abstract_evidence")

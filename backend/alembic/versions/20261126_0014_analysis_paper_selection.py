"""Persist the job-scoped finalized analysis paper set."""
from alembic import op
import sqlalchemy as sa

revision = "20261126_0014"
down_revision = "20261126_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("research_job_papers", sa.Column("analysis_selected", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    # Existing jobs are derived only from an already persisted PDF with evidence;
    # no source is moved between papers and no unavailable shortlist item is selected.
    op.execute("""UPDATE research_job_papers SET analysis_selected = 1
        WHERE EXISTS (SELECT 1 FROM source_documents d JOIN evidence_chunks c ON c.source_document_id = d.id
                      WHERE d.paper_id = research_job_papers.paper_id)""")


def downgrade():
    op.drop_column("research_job_papers", "analysis_selected")

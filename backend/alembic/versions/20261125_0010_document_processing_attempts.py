"""Add a durable, sanitized record of document processing attempts."""
from alembic import op
import sqlalchemy as sa

revision = '20261125_0010'
down_revision = '20261125_0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('document_processing_attempts',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('research_job_id',sa.String(36),sa.ForeignKey('research_jobs.id',ondelete='CASCADE'),nullable=False),
        sa.Column('paper_id',sa.String(36),sa.ForeignKey('papers.id',ondelete='CASCADE'),nullable=False),
        sa.Column('operation',sa.String(16),nullable=False), sa.Column('outcome',sa.String(16),nullable=False),
        sa.Column('stage',sa.String(32),nullable=False), sa.Column('error_code',sa.String(64)),
        sa.Column('retryable',sa.Boolean()), sa.Column('http_status',sa.Integer()),
        sa.Column('started_at',sa.DateTime(timezone=True),nullable=False), sa.Column('completed_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint("operation IN ('acquire', 'extract')",name='ck_document_attempt_operation'),
        sa.CheckConstraint("outcome IN ('started', 'succeeded', 'failed')",name='ck_document_attempt_outcome'))
    op.create_index('ix_document_processing_attempts_research_job_id','document_processing_attempts',['research_job_id'])
    op.create_index('ix_document_processing_attempts_paper_id','document_processing_attempts',['paper_id'])


def downgrade():
    op.drop_index('ix_document_processing_attempts_paper_id',table_name='document_processing_attempts')
    op.drop_index('ix_document_processing_attempts_research_job_id',table_name='document_processing_attempts')
    op.drop_table('document_processing_attempts')

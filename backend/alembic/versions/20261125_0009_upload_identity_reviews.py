"""Add auditable, human-confirmed upload associations.

Revision ID: 20261125_0009
Revises: 20261125_0008
"""
from alembic import op
import sqlalchemy as sa

revision = '20261125_0009'
down_revision = '20261125_0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('upload_identity_reviews',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_document_id', sa.String(36), sa.ForeignKey('source_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('research_job_id', sa.String(36), sa.ForeignKey('research_jobs.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('outcome', sa.String(32), nullable=False),
        sa.Column('reviewer_action', sa.String(64), nullable=False),
        sa.Column('document_hash', sa.String(64), nullable=False),
        sa.Column('comparison', sa.JSON(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome = 'human_confirmed'", name='ck_upload_review_outcome'))
    op.create_index('ix_upload_identity_reviews_source_document_id', 'upload_identity_reviews', ['source_document_id'])


def downgrade():
    op.drop_index('ix_upload_identity_reviews_source_document_id', table_name='upload_identity_reviews')
    op.drop_table('upload_identity_reviews')

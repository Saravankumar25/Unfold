"""initial schema

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("position", sa.Integer),
        sa.Column("text", sa.Text),
        sa.Column("token_count", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("documents")

"""project deleting marker and sweeper indexes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("projects", sa.Column("deleting_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_projects_deleting_at"), "projects", ["deleting_at"], unique=False)
    op.create_index(op.f("ix_projects_expires_at"), "projects", ["expires_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_projects_expires_at"), table_name="projects")
    op.drop_index(op.f("ix_projects_deleting_at"), table_name="projects")
    op.drop_column("projects", "deleting_at")

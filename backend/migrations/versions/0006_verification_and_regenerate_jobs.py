"""guide section verification, and typed analysis jobs for single-chapter regeneration (additive)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-11 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "guide_sections", sa.Column("verification", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "analysis_jobs",
        sa.Column("kind", sa.String(length=16), server_default="analysis", nullable=False),
    )
    op.add_column("analysis_jobs", sa.Column("section_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("analysis_jobs", "section_key")
    op.drop_column("analysis_jobs", "kind")
    op.drop_column("guide_sections", "verification")

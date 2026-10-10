"""guide section research brief and input hash (additive)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "guide_sections", sa.Column("research", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("guide_sections", sa.Column("input_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("guide_sections", "input_hash")
    op.drop_column("guide_sections", "research")

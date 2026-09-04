"""lessons created_at index

Revision ID: c3f6a1d8e2b7
Revises: 99abd72c629d
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f6a1d8e2b7'
down_revision: Union[str, Sequence[str], None] = '99abd72c629d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Phase 10 backlog item: GET /api/admin/lessons orders by
    lessons.created_at with no supporting index, unlike the identically-
    shaped conversations list (see f9824ef578ed).
    """
    op.create_index(op.f('ix_lessons_created_at'), 'lessons', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_lessons_created_at'), table_name='lessons')

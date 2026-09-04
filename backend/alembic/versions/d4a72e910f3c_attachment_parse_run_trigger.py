"""attachment parse run trigger

Revision ID: d4a72e910f3c
Revises: c3f6a1d8e2b7
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a72e910f3c'
down_revision: Union[str, Sequence[str], None] = 'c3f6a1d8e2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    gemini.parse()'s @span(SpanKind.PARSE, "gemini.parse") decorator
    hard-requires an active Run; an attachment upload has no ambient one
    (it happens before the chat turn it belongs to is sent), so it now
    owns a fresh one under this trigger when none is active -- the same
    pattern as 'lesson_edit' (99abd72c629d) for the identical reason.
    """
    op.execute("ALTER TYPE run_trigger ADD VALUE IF NOT EXISTS 'attachment_parse'")
    op.execute("COMMIT")


def downgrade() -> None:
    """Downgrade schema."""
    # An enum value cannot be removed from a Postgres type in place; leaving
    # 'attachment_parse' behind is harmless and is the standard downgrade
    # compromise for ALTER TYPE ... ADD VALUE (see 211125c17904's identical
    # note for 'approval_execution').

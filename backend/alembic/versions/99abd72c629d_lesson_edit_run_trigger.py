"""lesson edit run trigger

Revision ID: 99abd72c629d
Revises: f9824ef578ed
Create Date: 2026-09-04 10:47:13.505454

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '99abd72c629d'
down_revision: Union[str, Sequence[str], None] = 'f9824ef578ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Phase 9 Task 5 wires admin_patch_lesson/admin_archive_lesson to
    writer.upsert_embedding, whose real backend (McpChromaBackend) wraps
    every Chroma call in a tracing span; span() hard-requires an active Run,
    and unlike a chat turn, a dossier build, or a reflection, an admin
    PATCH/DELETE has none of its own. 'lesson_edit' is the trigger the
    endpoint's own Run uses (same pattern as 'approval_execution' below).
    """
    op.execute("ALTER TYPE run_trigger ADD VALUE IF NOT EXISTS 'lesson_edit'")
    op.execute("COMMIT")


def downgrade() -> None:
    """Downgrade schema."""
    # An enum value cannot be removed from a Postgres type in place; leaving
    # 'lesson_edit' behind is harmless and is the standard downgrade
    # compromise for ALTER TYPE ... ADD VALUE (see 211125c17904's identical
    # note for 'approval_execution').

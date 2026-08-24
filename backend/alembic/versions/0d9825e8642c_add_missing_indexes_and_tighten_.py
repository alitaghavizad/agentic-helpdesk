"""add missing indexes and tighten requester constraint

Adds two indexes flagged by the whole-branch review as missing on hot/
required paths (RefreshToken.token_hash, looked up on every refresh/logout;
Span.parent_span_id, required by design spec section 5.4), and tightens the
`conversations` requester CheckConstraint from "at least one of user_id /
guest fields" to "exactly one" (Postgres `<>` on booleans is XOR), per
design spec section 5.2. The constraint change is written by hand because
Alembic autogenerate does not diff CHECK constraint bodies, only presence
by name.

Revision ID: 0d9825e8642c
Revises: ae311bad86b0
Create Date: 2026-08-24 23:56:06.689401

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d9825e8642c'
down_revision: Union[str, Sequence[str], None] = 'ae311bad86b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f('ix_refresh_tokens_token_hash'), 'refresh_tokens', ['token_hash'], unique=False)
    op.create_index(op.f('ix_spans_parent_span_id'), 'spans', ['parent_span_id'], unique=False)

    op.drop_constraint('ck_conversations_requester_present', 'conversations', type_='check')
    op.create_check_constraint(
        'ck_conversations_requester_present',
        'conversations',
        '(user_id IS NOT NULL) <> (guest_name IS NOT NULL AND guest_email IS NOT NULL)',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_conversations_requester_present', 'conversations', type_='check')
    op.create_check_constraint(
        'ck_conversations_requester_present',
        'conversations',
        '(user_id IS NOT NULL) OR (guest_name IS NOT NULL AND guest_email IS NOT NULL)',
    )

    op.drop_index(op.f('ix_spans_parent_span_id'), table_name='spans')
    op.drop_index(op.f('ix_refresh_tokens_token_hash'), table_name='refresh_tokens')

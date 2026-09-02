"""add admin read path indexes

Every column the spec-15 admin read endpoints order by or filter on was
unindexed, so each of them was a sequential scan over a table that grows
without bound. `audit_log` is the worst case: spec 5.4 makes it append-only,
so it only ever gets longer, and its ordering column plus all three of its
exact-match filter columns had no index at all.

  audit_log.created_at   - the list's ORDER BY and the new date-range filter
  audit_log.action       - exact-match filter
  audit_log.target_type  - exact-match filter
  audit_log.actor_id     - exact-match filter ("what did actor X do")
  runs.started_at        - the run list's ORDER BY and every overview counter,
                           all of which are `started_at >= start-of-today`
  conversations.created_at - the conversation list's ORDER BY

Deliberately single-column rather than composite: the filters combine freely
(any subset of actor/action/target/date range), so a composite would only
help the one prefix order it was built for, while Postgres can bitmap-AND
several single-column indexes for any combination.

No index is added for the conversation search itself. That is an ILIKE
'%term%' across five columns including two on a joined table; a btree cannot
serve a leading-wildcard match, and the right tool would be a pg_trgm GIN
index -- a bigger decision (extension, write cost) than this migration should
make on its own.

Revision ID: f9824ef578ed
Revises: 211125c17904
Create Date: 2026-08-29 16:15:23.931765

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9824ef578ed'
down_revision: Union[str, Sequence[str], None] = '211125c17904'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f('ix_audit_log_created_at'), 'audit_log', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
    op.create_index(op.f('ix_audit_log_target_type'), 'audit_log', ['target_type'], unique=False)
    op.create_index(op.f('ix_audit_log_actor_id'), 'audit_log', ['actor_id'], unique=False)
    op.create_index(op.f('ix_runs_started_at'), 'runs', ['started_at'], unique=False)
    op.create_index(op.f('ix_conversations_created_at'), 'conversations', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_conversations_created_at'), table_name='conversations')
    op.drop_index(op.f('ix_runs_started_at'), table_name='runs')
    op.drop_index(op.f('ix_audit_log_actor_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_target_type'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')

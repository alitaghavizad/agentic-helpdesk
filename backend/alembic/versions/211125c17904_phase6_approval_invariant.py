"""phase6 approval invariant

Revision ID: 211125c17904
Revises: 0d9825e8642c
Create Date: 2026-08-27 15:54:15.232713

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '211125c17904'
down_revision: Union[str, Sequence[str], None] = '0d9825e8642c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # New enum value must be committed before any DDL below can reference it.
    op.execute("ALTER TYPE run_trigger ADD VALUE IF NOT EXISTS 'approval_execution'")
    op.execute("COMMIT")

    # Sole purpose: be a valid target for the composite FK below. id is
    # already the PK, so this constraint is trivially satisfied and costs
    # only the index Postgres builds for it.
    op.create_unique_constraint(
        "uq_approval_requests_id_status", "approval_requests", ["id", "status"],
    )

    # outbound_emails is empty at this point in the project's life (nothing
    # has ever written to it), so the column can be added NOT NULL with no
    # backfill. Guard anyway so a re-run against a populated table fails
    # loudly rather than silently inventing a status.
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM outbound_emails")).scalar_one()
    if count:
        raise RuntimeError(
            f"outbound_emails already has {count} rows; this migration assumes an "
            "empty table and has no backfill strategy for approval_status_at_send"
        )

    op.add_column(
        "outbound_emails",
        sa.Column(
            "approval_status_at_send",
            postgresql.ENUM(name="approval_status", create_type=False),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_outbound_emails_approval_status",
        "outbound_emails",
        "approval_requests",
        ["approval_request_id", "approval_status_at_send"],
        ["id", "status"],
        onupdate="CASCADE",
    )
    op.create_check_constraint(
        "ck_outbound_emails_approved_before_send",
        "outbound_emails",
        "approval_status_at_send IN ('approved', 'executed', 'failed')",
    )

    op.create_index(
        "ix_notifications_user_id_read_at", "notifications", ["user_id", "read_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_notifications_user_id_read_at", table_name="notifications")
    op.drop_constraint("ck_outbound_emails_approved_before_send", "outbound_emails", type_="check")
    op.drop_constraint("fk_outbound_emails_approval_status", "outbound_emails", type_="foreignkey")
    op.drop_column("outbound_emails", "approval_status_at_send")
    op.drop_constraint("uq_approval_requests_id_status", "approval_requests", type_="unique")
    # An enum value cannot be removed from a Postgres type in place; leaving
    # 'approval_execution' behind is harmless and is the standard downgrade
    # compromise for ALTER TYPE ... ADD VALUE.

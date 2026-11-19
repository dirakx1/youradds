"""Managed mode: the client approves before staff start spending."""
from alembic import op
import sqlalchemy as sa

revision = "0009_spend_approvals"
down_revision = "0008_staff_audit_log"
branch_labels = None
depends_on = None

UTC_NOW = sa.text("to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade():
    op.add_column("users", sa.Column("staff_activation", sa.Boolean(), nullable=False, server_default=sa.text("false")), schema="yourads")
    op.create_table(
        "approvals",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("yourads.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("campaign_id", sa.BigInteger(), sa.ForeignKey("yourads.campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("requested_at", sa.Text(), server_default=UTC_NOW),
        sa.Column("decided_at", sa.Text()),
        sa.CheckConstraint("status IN ('pending', 'approved', 'declined')", name="ck_approvals_status"),
        schema="yourads",
    )
    op.create_index("ix_approvals_account_id", "approvals", ["account_id", "id"], schema="yourads")


def downgrade():
    op.drop_index("ix_approvals_account_id", table_name="approvals", schema="yourads")
    op.drop_table("approvals", schema="yourads")
    op.drop_column("users", "staff_activation", schema="yourads")

"""Managed mode: audit log of what youradds staff do inside a customer's account."""
from alembic import op
import sqlalchemy as sa

revision = "0008_staff_audit_log"
down_revision = "0007_account_mode_links"
branch_labels = None
depends_on = None

UTC_NOW = sa.text("to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade():
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("at", sa.Text(), server_default=UTC_NOW),
        sa.Column("staff_email", sa.Text(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), sa.ForeignKey("yourads.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        schema="yourads",
    )
    op.create_index("ix_audit_log_account_id", "audit_log", ["account_id", "id"], schema="yourads")


def downgrade():
    op.drop_index("ix_audit_log_account_id", table_name="audit_log", schema="yourads")
    op.drop_table("audit_log", schema="yourads")

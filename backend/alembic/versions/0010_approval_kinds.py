"""Managed mode: approvals also cover budget increases, and remember the amount approved."""
from alembic import op
import sqlalchemy as sa

revision = "0010_approval_kinds"
down_revision = "0009_spend_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("approvals", sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'activate'")), schema="yourads")
    op.add_column("approvals", sa.Column("value", sa.Double()), schema="yourads")
    op.create_check_constraint("ck_approvals_kind", "approvals", "kind IN ('activate', 'budget')", schema="yourads")


def downgrade():
    op.drop_constraint("ck_approvals_kind", "approvals", schema="yourads")
    op.drop_column("approvals", "value", schema="yourads")
    op.drop_column("approvals", "kind", schema="yourads")

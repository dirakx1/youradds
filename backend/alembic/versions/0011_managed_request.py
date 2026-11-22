"""Managed mode: a client can ask youradds to manage their ads."""
from alembic import op
import sqlalchemy as sa

revision = "0011_managed_request"
down_revision = "0010_approval_kinds"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("managed_requested_at", sa.Text()), schema="yourads")


def downgrade():
    op.drop_column("users", "managed_requested_at", schema="yourads")

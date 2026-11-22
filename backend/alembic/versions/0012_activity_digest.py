"""Managed mode: remember which staff activity the client was already emailed about."""
from alembic import op
import sqlalchemy as sa

revision = "0012_activity_digest"
down_revision = "0011_managed_request"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("activity_notified_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")), schema="yourads")


def downgrade():
    op.drop_column("users", "activity_notified_id", schema="yourads")

"""Multi-user foundations: account mode, token expiry/source, and one youradds account per connected ad account."""
from alembic import op
import sqlalchemy as sa

revision = "0007_account_mode_links"
down_revision = "0006_meta_publish"
branch_labels = None
depends_on = None

UTC_NOW = sa.text("to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade():
    op.add_column("users", sa.Column("mode", sa.Text(), nullable=False, server_default=sa.text("'self'")), schema="yourads")
    op.create_check_constraint("ck_users_mode", "users", "mode IN ('self', 'managed')", schema="yourads")
    op.add_column("credentials", sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'manual'")), schema="yourads")
    op.add_column("credentials", sa.Column("expires_at", sa.Text()), schema="yourads")
    op.create_table(
        "connection_links",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("yourads.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), server_default=UTC_NOW),
        sa.PrimaryKeyConstraint("user_id", "service", "kind", name="pk_connection_links"),
        sa.UniqueConstraint("service", "kind", "external_id", name="uq_connection_links_external"),
        schema="yourads",
    )


def downgrade():
    op.drop_table("connection_links", schema="yourads")
    op.drop_column("credentials", "expires_at", schema="yourads")
    op.drop_column("credentials", "source", schema="yourads")
    op.drop_constraint("ck_users_mode", "users", schema="yourads")
    op.drop_column("users", "mode", schema="yourads")

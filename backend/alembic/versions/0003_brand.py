"""Add the per-user brand profile ("Mi marca")."""
from alembic import op
import sqlalchemy as sa

revision = "0003_brand"
down_revision = "0002_clerk_identity"
branch_labels = None
depends_on = None

UTC_NOW = sa.text("to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade():
    op.create_table(
        "brands",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("yourads.users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("story", sa.Text()),
        sa.Column("tone", sa.Text()),
        sa.Column("language", sa.Text(), nullable=False, server_default=sa.text("'es'")),
        sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'COP'")),
        sa.Column("palette", sa.Text()),
        sa.Column("fonts", sa.Text()),
        sa.Column("ideal_customer", sa.Text()),
        sa.Column("objections", sa.Text()),
        sa.Column("limits", sa.Text()),
        sa.Column("products", sa.Text()),
        sa.Column("reviews", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("updated_at", sa.Text(), server_default=UTC_NOW),
        schema="yourads",
    )


def downgrade():
    op.drop_table("brands", schema="yourads")

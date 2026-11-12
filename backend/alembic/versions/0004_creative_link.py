"""Where a creative's button goes (web page or WhatsApp link)."""
from alembic import op
import sqlalchemy as sa

revision = "0004_creative_link"
down_revision = "0003_brand"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("creatives", sa.Column("link", sa.Text()), schema="yourads")


def downgrade():
    op.drop_column("creatives", "link", schema="yourads")

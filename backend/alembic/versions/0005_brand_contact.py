"""Brand website and WhatsApp number (default destination of ad buttons)."""
from alembic import op
import sqlalchemy as sa

revision = "0005_brand_contact"
down_revision = "0004_creative_link"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("brands", sa.Column("website", sa.Text()), schema="yourads")
    op.add_column("brands", sa.Column("whatsapp", sa.Text()), schema="yourads")


def downgrade():
    op.drop_column("brands", "whatsapp", schema="yourads")
    op.drop_column("brands", "website", schema="yourads")

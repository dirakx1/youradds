"""Meta publishing state: what was created in Meta for a campaign, and the image hashes of its creatives."""
from alembic import op
import sqlalchemy as sa

revision = "0006_meta_publish"
down_revision = "0005_brand_contact"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaigns", sa.Column("publish", sa.Text()), schema="yourads")
    op.add_column("creatives", sa.Column("image_hash", sa.Text()), schema="yourads")


def downgrade():
    op.drop_column("creatives", "image_hash", schema="yourads")
    op.drop_column("campaigns", "publish", schema="yourads")

"""Add Clerk subject mapping while preserving local account IDs and history."""
from alembic import op
import sqlalchemy as sa

revision = "0002_clerk_identity"
down_revision = "0001_postgres"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("clerk_user_id", sa.Text(), nullable=True), schema="yourads")
    op.create_unique_constraint("uq_users_clerk_user_id", "users", ["clerk_user_id"], schema="yourads")
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True, schema="yourads")


def downgrade():
    op.execute(sa.text('''DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM "yourads"."users" WHERE "password_hash" IS NULL) THEN
            RAISE EXCEPTION 'cannot downgrade Clerk identity migration while users have NULL password hashes';
        END IF;
    END $$'''))
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False, schema="yourads")
    op.drop_constraint("uq_users_clerk_user_id", "users", schema="yourads", type_="unique")
    op.drop_column("users", "clerk_user_id", schema="yourads")

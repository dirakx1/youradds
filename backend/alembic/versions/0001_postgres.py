"""Create the isolated application schema and its initial PostgreSQL tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0001_postgres"
down_revision = None
branch_labels = None
depends_on = None
S = "yourads"
UTC = sa.text("to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade():
    op.execute(sa.schema.CreateSchema(S, if_not_exists=True))
    op.execute(sa.text(f'REVOKE ALL ON SCHEMA "{S}" FROM PUBLIC'))
    op.create_table("users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False), sa.Column("name", sa.Text()),
        sa.Column("password_hash", sa.Text(), nullable=False), sa.Column("webhook_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), server_default=UTC),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("webhook_key", name="uq_users_webhook_key"), schema=S)
    op.create_table("sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False), sa.Column("created_at", sa.Text(), server_default=UTC), schema=S)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], schema=S)
    op.create_table("credentials",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("service", sa.Text(), primary_key=True), sa.Column("data", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), server_default=UTC), schema=S)
    op.create_table("campaigns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("platform", sa.Text(), nullable=False), sa.Column("external_id", sa.Text()),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE")),
        sa.Column("demo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("objective", sa.Text(), nullable=False, server_default=sa.text("'sales'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("daily_budget", sa.Double(), nullable=False, server_default="20"),
        sa.Column("headline", sa.Text()), sa.Column("body", sa.Text()), sa.Column("cta", sa.Text()),
        sa.Column("landing_url", sa.Text()), sa.Column("audience", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=UTC), schema=S)
    op.create_index("ix_campaigns_user_id_id", "campaigns", ["user_id", "id"], schema=S)
    op.create_index("ix_campaigns_user_external_id", "campaigns", ["user_id", "external_id"], schema=S)
    op.create_table("metrics",
        sa.Column("campaign_id", sa.BigInteger(), sa.ForeignKey(f"{S}.campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Text(), nullable=False), sa.Column("impressions", sa.BigInteger()),
        sa.Column("clicks", sa.BigInteger()), sa.Column("spend", sa.Double()),
        sa.Column("conversions", sa.BigInteger()), sa.Column("revenue", sa.Double()),
        sa.PrimaryKeyConstraint("campaign_id", "day", name="pk_metrics"), schema=S)
    op.create_table("leads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE")),
        sa.Column("phone", sa.Text(), nullable=False), sa.Column("name", sa.Text()),
        sa.Column("campaign_id", sa.BigInteger(), sa.ForeignKey(f"{S}.campaigns.id", ondelete="SET NULL")),
        sa.Column("referral", sa.Text()), sa.Column("stage", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("handoff", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opted_out", sa.Integer(), nullable=False, server_default="0"), sa.Column("value", sa.Double()),
        sa.Column("followups", sa.Integer(), nullable=False, server_default="0"), sa.Column("last_inbound_at", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=UTC),
        sa.UniqueConstraint("user_id", "phone", name="uq_leads_user_phone"), schema=S)
    op.create_index("ix_leads_user_id_id", "leads", ["user_id", "id"], schema=S)
    op.create_table("messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("lead_id", sa.BigInteger(), sa.ForeignKey(f"{S}.leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False), sa.Column("sender", sa.Text(), nullable=False, server_default=sa.text("'lead'")),
        sa.Column("body", sa.Text(), nullable=False), sa.Column("wa_id", sa.Text()), sa.Column("status", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=UTC),
        sa.UniqueConstraint("wa_id", name="uq_messages_wa_id"), schema=S)
    op.create_index("ix_messages_lead_id_id", "messages", ["lead_id", "id"], schema=S)
    op.create_table("creatives",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE")),
        sa.Column("platform", sa.Text(), nullable=False), sa.Column("format", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()), sa.Column("file", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text()), sa.Column("subheadline", sa.Text()), sa.Column("cta", sa.Text()),
        sa.Column("language", sa.Text()),
        sa.Column("campaign_id", sa.BigInteger(), sa.ForeignKey(f"{S}.campaigns.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.Text(), server_default=UTC), schema=S)
    op.create_index("ix_creatives_user_id_id", "creatives", ["user_id", "id"], schema=S)
    op.create_table("videos",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(f"{S}.users.id", ondelete="CASCADE")),
        sa.Column("platform", sa.Text(), nullable=False), sa.Column("format", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()), sa.Column("ratio", sa.Text()),
        sa.Column("duration", sa.Integer()), sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("error", sa.Text()), sa.Column("file", sa.Text()), sa.Column("headline", sa.Text()),
        sa.Column("cta", sa.Text()), sa.Column("cost_usd", sa.Double()), sa.Column("created_at", sa.Text(), server_default=UTC),
        sa.Column("job", sa.Text()), sa.Column("request_id", sa.Text()), sa.Column("status_url", sa.Text()),
        sa.Column("cancel_url", sa.Text()), sa.Column("provider_status", sa.Text()), sa.Column("params", sa.Text()),
        sa.Column("started_at", sa.Text()), schema=S)
    op.create_index("ix_videos_user_id_id", "videos", ["user_id", "id"], schema=S)
    op.create_table("settings", sa.Column("key", sa.Text(), primary_key=True), sa.Column("value", sa.Text()), schema=S)

    op.execute(sa.text(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{S}" FROM PUBLIC'))
    op.execute(sa.text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA "{S}" FROM PUBLIC'))
    for role in ("anon", "authenticated"):
        op.execute(sa.text(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                            f'REVOKE ALL ON SCHEMA "{S}" FROM "{role}"; '
                            f'REVOKE ALL ON ALL TABLES IN SCHEMA "{S}" FROM "{role}"; '
                            f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA "{S}" FROM "{role}"; '
                            "END IF; END $$"))


def downgrade():
    for table in ("settings", "videos", "creatives", "messages", "leads", "metrics", "campaigns",
                  "credentials", "sessions", "users"):
        op.drop_table(table, schema=S)

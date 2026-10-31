"""SQLAlchemy mappings for the application-owned ``yourads`` schema."""
from sqlalchemy import BigInteger, Double, ForeignKey, Identity, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "yourads"
UTC_NOW = text("to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),
                      UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
                      UniqueConstraint("webhook_key", name="uq_users_webhook_key"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    clerk_user_id: Mapped[str | None] = mapped_column(Text)
    webhook_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"), {"schema": SCHEMA})
    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = {"schema": SCHEMA}
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True)
    service: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_user_id_id", "user_id", "id"),
        Index("ix_campaigns_user_external_id", "user_id", "external_id"),
        {"schema": SCHEMA},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"))
    demo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'sales'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    daily_budget: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("20"))
    headline: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(Text)
    landing_url: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = {"schema": SCHEMA}
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.campaigns.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[str] = mapped_column(Text, primary_key=True)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    clicks: Mapped[int | None] = mapped_column(BigInteger)
    spend: Mapped[float | None] = mapped_column(Double)
    conversions: Mapped[int | None] = mapped_column(BigInteger)
    revenue: Mapped[float | None] = mapped_column(Double)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("user_id", "phone", name="uq_leads_user_phone"), Index("ix_leads_user_id_id", "user_id", "id"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"))
    phone: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    campaign_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.campaigns.id", ondelete="SET NULL"))
    referral: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'new'"))
    handoff: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    opted_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    value: Mapped[float | None] = mapped_column(Double)
    followups: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_inbound_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("wa_id", name="uq_messages_wa_id"), Index("ix_messages_lead_id_id", "lead_id", "id"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    lead_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.leads.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    sender: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'lead'"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    wa_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class Creative(Base):
    __tablename__ = "creatives"
    __table_args__ = (Index("ix_creatives_user_id_id", "user_id", "id"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    file: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str | None] = mapped_column(Text)
    subheadline: Mapped[str | None] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    campaign_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.campaigns.id", ondelete="SET NULL"))
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (Index("ix_videos_user_id_id", "user_id", "id"), {"schema": SCHEMA})
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    ratio: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    error: Mapped[str | None] = mapped_column(Text)
    file: Mapped[str | None] = mapped_column(Text)
    headline: Mapped[str | None] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[str | None] = mapped_column(Text, server_default=UTC_NOW)
    job: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    status_url: Mapped[str | None] = mapped_column(Text)
    cancel_url: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(Text)
    params: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = {"schema": SCHEMA}
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)

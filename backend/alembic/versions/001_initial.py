"""initial warehouse schema

Revision ID: 001_initial
Revises:
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "objects",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("objects.id"), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("system_type", sa.String(128), nullable=False),
        sa.Column("sensor_type", sa.String(128), nullable=False),
        sa.Column("tag", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("object_id", sa.BigInteger(), sa.ForeignKey("objects.id")),
        sa.Column("picket", sa.String(64)),
    )
    op.create_index("ix_channels_object_id", "channels", ["object_id"])
    op.create_table(
        "events_alarm",
        sa.Column("event_id", sa.BigInteger(), primary_key=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("event_ts", sa.DateTime(), nullable=False),
        sa.Column("value_raw", sa.Text()),
        sa.Column("value_num", sa.Float()),
        sa.Column("value_cat", sa.String(255)),
    )
    op.create_index("ix_events_alarm_channel_id", "events_alarm", ["channel_id"])
    op.create_index("ix_events_alarm_event_ts", "events_alarm", ["event_ts"])
    op.create_table(
        "events_numeric_hourly",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("hour_ts", sa.DateTime(), nullable=False),
        sa.Column("n_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_alarms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("value_mean", sa.Float()),
        sa.Column("value_min", sa.Float()),
        sa.Column("value_max", sa.Float()),
        sa.UniqueConstraint("channel_id", "hour_ts", name="uq_hourly_channel_hour"),
    )
    op.create_index("ix_hourly_channel_id", "events_numeric_hourly", ["channel_id"])
    op.create_index("ix_hourly_hour_ts", "events_numeric_hourly", ["hour_ts"])
    op.create_table(
        "meteo_daily",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("temp_mean", sa.Float()),
        sa.Column("precipitation_mm", sa.Float()),
        sa.Column("humidity", sa.Float()),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
    )
    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("object_id", sa.BigInteger()),
        sa.Column("channel_id", sa.BigInteger()),
        sa.Column("risk_type", sa.String(64), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("horizon_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("explanation", sa.Text()),
        sa.Column("recommendation", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_predictions_object_id", "predictions", ["object_id"])
    op.create_table(
        "dispatcher_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prediction_id", sa.Integer(), sa.ForeignKey("predictions.id")),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("user_login", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "ticket_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("prediction_id", sa.Integer(), sa.ForeignKey("predictions.id")),
        sa.Column("object_id", sa.BigInteger()),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_login", sa.String(64)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.execute(
        """
        INSERT INTO users (login, role, display_name) VALUES
        ('dispatcher', 'dispatcher', 'Диспетчер ОДС'),
        ('analyst', 'analyst', 'Аналитик'),
        ('manager', 'manager', 'Руководитель')
        """
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("ticket_drafts")
    op.drop_table("dispatcher_feedback")
    op.drop_table("predictions")
    op.drop_table("users")
    op.drop_table("meteo_daily")
    op.drop_table("events_numeric_hourly")
    op.drop_table("events_alarm")
    op.drop_table("channels")
    op.drop_table("objects")

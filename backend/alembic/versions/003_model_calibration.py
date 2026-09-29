"""фаза 7: калибровка порогов LightGBM по решениям диспетчера

Revision ID: 003_model_calibration
Revises: 002_api_fields
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa

revision = "003_model_calibration"
down_revision = "002_api_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_calibration",
        sa.Column("risk_type", sa.String(64), primary_key=True),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("n_dispatch", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_false_alarm", sa.Integer, nullable=False, server_default="0"),
        sa.Column("user_login", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("model_calibration")

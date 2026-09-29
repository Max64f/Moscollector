"""фаза 5: source прогноза и статус заявки

Revision ID: 002_api_fields
Revises: 001_initial
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa

revision = "002_api_fields"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("source", sa.String(32), nullable=False, server_default="lgbm"),
    )
    op.add_column(
        "ticket_drafts",
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
    )
    op.add_column(
        "ticket_drafts",
        sa.Column("external_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticket_drafts", "external_id")
    op.drop_column("ticket_drafts", "status")
    op.drop_column("predictions", "source")

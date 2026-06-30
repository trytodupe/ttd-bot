"""init_keep_alive

迁移 ID: 0b2f4dcb12a5
父迁移:
创建时间: 2026-07-01 00:08:00.273136

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0b2f4dcb12a5"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("keep_alive",)
depends_on: str | Sequence[str] | None = None


def upgrade(name: str = "") -> None:
    if name:
        return
    op.create_table(
        "keep_alive_subscriber",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(), nullable=False),
        sa.Column("last_delivered", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keep_alive_subscriber")),
        sa.UniqueConstraint("user_id", name="uq_keep_alive_user"),
        info={"bind_key": "keep_alive"},
    )


def downgrade(name: str = "") -> None:
    if name:
        return
    op.drop_table("keep_alive_subscriber")

from datetime import date, datetime

from nonebot_plugin_orm import Model
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class KeepAliveSubscriber(Model):
    __tablename__ = "keep_alive_subscriber"
    __table_args__ = (UniqueConstraint("user_id", name="uq_keep_alive_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64))
    subscribed_at: Mapped[datetime]
    last_delivered: Mapped[date | None] = mapped_column(nullable=True)

from datetime import date, datetime
from typing import cast

from nonebot import get_bots, get_driver, on_command, require
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.log import logger
from nonebot.rule import is_type
from nonebot_plugin_orm import get_session
from sqlalchemy import select

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from .model import KeepAliveSubscriber  # noqa: E402

# ── toggle matcher ──────────────────────────────────────────────────────────

keep_alive_cmd = on_command(
    "续火",
    rule=is_type(PrivateMessageEvent),
    priority=10,
    block=True,
)


@keep_alive_cmd.handle()
async def _handle_toggle(event: PrivateMessageEvent) -> None:
    user_id = str(event.user_id)

    async with get_session() as session:
        stmt = select(KeepAliveSubscriber).where(
            KeepAliveSubscriber.user_id == user_id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            await session.delete(existing)
            await session.commit()
            await keep_alive_cmd.finish("已取消续火")
        else:
            session.add(
                KeepAliveSubscriber(
                    user_id=user_id,
                    subscribed_at=datetime.utcnow(),
                )
            )
            await session.commit()
            await keep_alive_cmd.finish("已开启续火")


# ── delivery logic ──────────────────────────────────────────────────────────

def _select_bot() -> Bot | None:
    bots = get_bots()
    if not bots:
        return None
    return cast(Bot, next(iter(bots.values())))


async def _send_to_subscribers() -> None:
    today = date.today()
    bot = _select_bot()
    if not bot:
        logger.warning("keep_alive: no bot available for delivery")
        return

    async with get_session() as session:
        stmt = select(KeepAliveSubscriber).where(
            (KeepAliveSubscriber.last_delivered < today)
            | (KeepAliveSubscriber.last_delivered.is_(None))
        )
        result = await session.execute(stmt)
        subscribers = result.scalars().all()

        if not subscribers:
            return

        sent = 0
        for sub in subscribers:
            try:
                await bot.call_api(
                    "send_private_msg",
                    user_id=int(sub.user_id),
                    message="1",
                )
                sub.last_delivered = today
                sent += 1
            except Exception as e:
                logger.warning(f"keep_alive: failed to send to {sub.user_id}: {e}")

        await session.commit()
        logger.info(f"keep_alive: delivered to {sent}/{len(subscribers)} subscribers")


# ── scheduler ───────────────────────────────────────────────────────────────

_JOB_ID = "keep_alive_send"


@get_driver().on_startup
async def _setup_scheduler() -> None:
    # first run at 12:05 UTC+8 (04:05 UTC), then every 30 min until 07:30+1 UTC+8
    scheduler.add_job(
        _send_to_subscribers,
        "cron",
        minute="5,35",
        hour="4-23",
        id=_JOB_ID,
        replace_existing=True,
        misfire_grace_time=1800,
    )


@get_driver().on_shutdown
async def _teardown_scheduler() -> None:
    job = scheduler.get_job(_JOB_ID)
    if job:
        job.remove()

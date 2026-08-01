from nonebot import get_driver, get_plugin_config
from nonebot_plugin_apscheduler import scheduler

from .commands import register_commands
from .config import Config
from .service import check_dynamic_updates, check_live_updates
from .storage import SubscriptionManager

LIVE_JOB_ID = "bilibili-subscription-live-check"
DYNAMIC_JOB_ID = "bilibili-subscription-dynamic-check"

plugin_config = get_plugin_config(Config)
subscription_manager = SubscriptionManager()
register_commands(subscription_manager)


async def scheduled_live_check() -> None:
    if plugin_config.parser_bili_sub_enabled:
        await check_live_updates(subscription_manager)


async def scheduled_dynamic_check() -> None:
    if plugin_config.parser_bili_sub_enabled:
        await check_dynamic_updates(subscription_manager)


@get_driver().on_startup
async def register_jobs() -> None:
    scheduler.add_job(
        scheduled_live_check,
        "interval",
        seconds=plugin_config.parser_bili_live_interval,
        id=LIVE_JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_dynamic_check,
        "interval",
        seconds=plugin_config.parser_bili_sub_interval,
        id=DYNAMIC_JOB_ID,
        replace_existing=True,
    )


@get_driver().on_shutdown
async def remove_jobs() -> None:
    for job_id in (LIVE_JOB_ID, DYNAMIC_JOB_ID):
        if scheduler.get_job(job_id) is not None:
            scheduler.remove_job(job_id)

from nonebot import get_plugin_config, require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")
require("nonebot_plugin_uninfo")
require("nonebot_plugin_parser")

from nonebot_plugin_apscheduler import scheduler

from .commands import register_commands
from .config import Config
from .service import check_dynamic_updates, check_live_updates
from .storage import SubscriptionManager

__plugin_meta__ = PluginMetadata(
    name="Bilibili UP subscription",
    description="Poll subscribed Bilibili users and notify groups of new posts and live sessions.",
    usage="ttd sub add/remove/list",
    config=Config,
)

plugin_config = get_plugin_config(Config)
subscription_manager = SubscriptionManager()
register_commands(subscription_manager)


@scheduler.scheduled_job(
    "interval",
    seconds=plugin_config.parser_bili_live_interval,
    id="bilibili-subscription-live-check",
)
async def scheduled_live_check() -> None:
    if plugin_config.parser_bili_sub_enabled:
        await check_live_updates(subscription_manager)


@scheduler.scheduled_job(
    "interval",
    seconds=plugin_config.parser_bili_sub_interval,
    id="bilibili-subscription-dynamic-check",
)
async def scheduled_dynamic_check() -> None:
    if plugin_config.parser_bili_sub_enabled:
        await check_dynamic_updates(subscription_manager)

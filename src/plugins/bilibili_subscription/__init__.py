from nonebot import require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")
require("nonebot_plugin_uninfo")
require("nonebot_plugin_parser")

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="Bilibili UP subscription",
    description="Poll subscribed Bilibili users and notify groups of new posts and live sessions.",
    usage="ttd sub add/remove/list",
    config=Config,
)

from . import __main__ as main  # noqa: F401

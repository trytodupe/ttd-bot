from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import Config


__plugin_meta__ = PluginMetadata(
    name="citation-counter",
    description="",
    usage="ttd cite [today|yesterday|total]",
    config=Config,
)

config = get_plugin_config(Config)

from . import __main__ as main

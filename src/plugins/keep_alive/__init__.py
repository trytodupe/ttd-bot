from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="续火",
    description="每日续火提醒",
    usage="在私聊中发送「续火」开启/关闭",
)

from . import __main__  # noqa: E402, F401
from . import model  # noqa: E402, F401 — ensure model is registered with ORM

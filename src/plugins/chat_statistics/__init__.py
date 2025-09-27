from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import Config


__plugin_meta__ = PluginMetadata(
    name="chat-statistics",
    description="获取用户聊天时间分布统计",
    usage="ttd chat [天数] - 生成综合统计图片(包含消息统计和活跃时间)\n支持 @用户 查看指定用户的统计数据\n如果无法生成图片会自动返回文本版本",
    config=Config,
)

config = get_plugin_config(Config)

# Import main module to register handlers
from . import __main__  # noqa: E402, F401

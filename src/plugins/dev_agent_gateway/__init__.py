from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="ttd development agent gateway",
    description="Internal fail-closed QQ gateway for the isolated development agent.",
    usage="Not advertised. Disabled unless DEV_AGENT_ENABLED=true.",
)

from . import __main__ as __main__

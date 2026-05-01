from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Visibility = Literal["public", "admin", "background", "internal"]


@dataclass(frozen=True)
class FeatureDoc:
    key: str
    title: str
    description: str
    providers: tuple[str, ...]
    visibility: Visibility = "public"
    commands: tuple[str, ...] = ()
    more_info: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


FEATURE_DOCS: tuple[FeatureDoc, ...] = (
    FeatureDoc(
        key="help",
        title="帮助索引",
        description="查看 ttd-bot 的功能列表和具体用法。",
        providers=("ttd_help",),
        commands=("ttd help", "ttd help <功能名>"),
    ),
    FeatureDoc(
        key="ping",
        title="自动提醒",
        description="配置关键词别名，群聊中出现别名时自动 @ 对应用户。",
        providers=("auto_ping",),
        visibility="admin",
        commands=(
            "ttd ping add <QQ号|@用户> <别名>",
            "ttd ping remove <别名>",
            "ttd ping list",
        ),
    ),
    FeatureDoc(
        key="react",
        title="自动回应",
        description="对指定用户的消息自动添加表情回应。",
        providers=("auto_react",),
        visibility="admin",
        commands=("ttd react add <QQ号>", "ttd react remove <QQ号>"),
    ),
    FeatureDoc(
        key="chat",
        title="聊天统计",
        description="生成指定用户最近若干天的消息分布和活跃时间统计图。",
        providers=("chat_statistics",),
        commands=("ttd chat [天数]", "ttd chat [天数] @用户"),
    ),
    FeatureDoc(
        key="cite",
        title="引用计数",
        description="统计本群回复引用次数。",
        providers=("citation_counter",),
        commands=("ttd cite", "ttd cite today", "ttd cite yesterday", "ttd cite total"),
    ),
    FeatureDoc(
        key="coc_apk",
        title="部落冲突 APK 检查",
        description="定时检查 Clash of Clans APK 更新并上传到目标群。",
        providers=("coc_apk_checker",),
        visibility="background",
    ),
    FeatureDoc(
        key="easy_trigger",
        title="轻量触发器",
        description="不告诉你。",
        providers=("easy_trigger",),
        visibility="background",
    ),
    FeatureDoc(
        key="mc",
        title="Minecraft 服务器状态",
        description="查询并监控群内配置的 Minecraft Java 服务器状态。",
        providers=("mc_server_checker",),
        commands=("ttd mc add <服务器地址>", "ttd mc remove <服务器地址>", "福"),
    ),
    FeatureDoc(
        key="release_note",
        title="版本更新提醒",
        description="启动时检查版本更新并发布更新日志。",
        providers=("release_note",),
        visibility="background",
        commands=("检查更新",),
    ),
    FeatureDoc(
        key="sticker_to_image",
        title="表情转图片",
        description="把表情包转换为普通图片。",
        providers=("sticker_to_image",),
        commands=("私聊发送表情", "群聊 @机器人 并发送表情"),
    ),
    FeatureDoc(
        key="tetr",
        title="TETR.IO 查询",
        description="绑定并查询 TETR.IO 玩家数据。",
        providers=("tetr_chercher",),
        commands=("ttd tetr bind <用户名>", "ttd tetr", "tetr"),
    ),
    FeatureDoc(
        key="etx",
        title="Elitebotix 查询",
        description="查询 osu! 玩家 Elitebotix duel rating。",
        providers=("_etx_query",),
        commands=("etx <osu! 用户名>",),
    ),
    FeatureDoc(
        key="quickmatch",
        title="osu! Quickmatch 查询",
        description="查询 osu! 玩家 quickmatch 数据。",
        providers=("_quickmatch_query",),
        commands=("qm <osu! 用户名>", "quickmatch <osu! 用户名>"),
    ),
    FeatureDoc(
        key="status",
        title="服务器状态",
        description="查看机器人所在服务器状态。",
        providers=("nonebot_plugin_status",),
        commands=("status", "状态", "戳一戳")
    ),
    FeatureDoc(
        key="wordcloud",
        title="词云",
        description="根据群消息生成词云。",
        providers=("nonebot_plugin_wordcloud",),
        commands=("今日词云", "昨日词云", "历史词云 [日期或时间段]", "我的今日词云"),
        more_info='更多用法见依赖插件内置帮助：发送 "词云"。',
    ),
    FeatureDoc(
        key="add_friends",
        title="好友与群邀请管理",
        description="处理好友申请和群邀请，支持查看、同意、拒绝和清空待处理申请。",
        providers=("nonebot_plugin_add_friends",),
        visibility="admin",
        commands=(
            "查看申请",
            "同意申请 <QQ号|群号>",
            "拒绝申请 <QQ号|群号>",
            "同意全部申请",
            "拒绝全部申请",
            "清空申请列表",
        ),
    ),
    FeatureDoc(
        key="authrespond",
        title="插件响应权限",
        description="管理插件级和群级响应黑白名单。",
        providers=("nonebot_plugin_authrespond",),
        visibility="admin",
        commands=("#<插件>拉黑<用户>", "#<插件>加白<用户>", "#<插件>封禁群", "#<插件>加群白"),
    ),
    FeatureDoc(
        key="auto_enter_group",
        title="加群自动审批",
        description="根据关键词自动审批入群请求，并支持退群黑名单。",
        providers=("nonebot_plugin_auto_enter_group",),
        visibility="admin",
        commands=(
            "查看关键词",
            "添加允许关键词 <关键词>",
            "删除允许关键词 <关键词>",
            "添加拒绝关键词 <关键词>",
            "删除拒绝关键词 <关键词>",
            "启用退群黑名单",
            "禁用退群黑名单",
        ),
    ),
    FeatureDoc(
        key="deer",
        title="鹿管签到",
        description="鹿管签到、排行和补签。",
        providers=("nonebot_plugin_deer_pipe",),
        commands=("🦌", "鹿", "🦌榜", "🦌历", "补🦌 <日期>", "🦌帮助"),
        more_info='更多用法见依赖插件内置帮助：发送 "🦌帮助" 或 "鹿帮助"。',
    ),
    FeatureDoc(
        key="groupmate_waifu",
        title="娶群友",
        description="随机娶一个群友做老婆。",
        providers=("nonebot_plugin_groupmate_waifu",),
        commands=("娶群友", "透群友"),
    ),
    FeatureDoc(
        key="steam",
        title="Steam 游戏状态",
        description="绑定 Steam 用户并播报群友游戏状态，也可解析 Steam 商店链接。",
        providers=("nonebot_plugin_steam_game_status",),
        commands=(
            "steam add <Steam ID>",
            "steam del <Steam ID>",
            "steam list",
        ),
        more_info='更多用法见依赖插件主页：https://github.com/nek0us/nonebot-plugin-steam-game-status',
    ),
    FeatureDoc(
        key="parser",
        title="链接解析",
        description="自动解析 B 站、抖音、快手、微博、小红书、YouTube、TikTok、Twitter、AcFun、NGA 等链接。",
        providers=("nonebot_plugin_parser",),
        commands=("发送支持平台的链接、BV号、小程序或卡片",),
    ),
    FeatureDoc(
        key="moellmchats",
        title="群聊 LLM",
        description="在群聊中 @ 机器人进行多轮上下文对话。",
        providers=("nonebot_plugin_moellmchats",),
        commands=("@机器人 <消息>",),
    ),
)

IGNORED_PROVIDERS: frozenset[str] = frozenset(
    {
        "nonebot-plugin-auto-sendlike",
        "nonebot_plugin_chatrecorder",
        "nonebot_plugin_clovers",
        "nonebot_plugin_datastore",
        "nonebot_plugin_localstore",
        "nonebot_plugin_orm",
        "nonebot_plugin_uninfo",
    }
)

_DOCS_BY_KEY = {doc.key: doc for doc in FEATURE_DOCS}
_PROVIDER_TO_DOC_KEYS: dict[str, list[str]] = {}
for _doc in FEATURE_DOCS:
    for _provider in _doc.providers:
        _PROVIDER_TO_DOC_KEYS.setdefault(_provider, []).append(_doc.key)


def get_feature_doc(key: str) -> FeatureDoc | None:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _DOCS_BY_KEY:
        return _DOCS_BY_KEY[normalized]
    doc_keys = _PROVIDER_TO_DOC_KEYS.get(normalized)
    if doc_keys:
        return _DOCS_BY_KEY[doc_keys[0]]
    for doc in FEATURE_DOCS:
        if normalized == doc.title.lower():
            return doc
    return None


def iter_visible_docs(*, include_admin: bool = False, include_background: bool = False) -> list[FeatureDoc]:
    docs: list[FeatureDoc] = []
    for doc in FEATURE_DOCS:
        if doc.visibility == "internal":
            continue
        if doc.visibility == "admin" and not include_admin:
            continue
        if doc.visibility == "background" and not include_background:
            continue
        docs.append(doc)
    return docs


def documented_providers() -> set[str]:
    return {provider for doc in FEATURE_DOCS for provider in doc.providers}

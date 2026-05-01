import sys
import tomllib
from pathlib import Path

import nonebot

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PROJECT_ROOT / "src" / "plugins"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(superusers={"12345"})

from ttd_help.formatter import format_doc_detail, format_help_index
from ttd_help import _is_long_message
from ttd_help.registry import FEATURE_DOCS, IGNORED_PROVIDERS, documented_providers, get_feature_doc


def _configured_nonebot_plugins() -> set[str]:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return set(pyproject["tool"]["nonebot"]["plugins"])


def _local_plugin_dirs() -> set[str]:
    return {
        path.name
        for path in PLUGIN_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "__pycache__"
    }


def test_ttd_help_registry_covers_all_configured_and_local_plugins():
    known_providers = _configured_nonebot_plugins() | _local_plugin_dirs()
    covered_providers = documented_providers() | set(IGNORED_PROVIDERS)

    assert known_providers - covered_providers == set()


def test_ttd_help_registry_has_no_duplicate_keys_or_providers():
    keys = [doc.key for doc in FEATURE_DOCS]
    providers = [provider for doc in FEATURE_DOCS for provider in doc.providers]

    assert len(keys) == len(set(keys))
    assert len(providers) == len(set(providers))


def test_ttd_help_index_is_chinese_and_hides_admin_by_default():
    text = format_help_index()

    assert "ttd-bot 帮助" in text
    assert "常用功能" in text
    assert "用法：" in text
    assert "ttd chat [天数]" in text
    assert '更多用法见依赖插件内置帮助：发送 "词云"。' in text
    assert "管理员可发送 ttd help admin 查看管理功能" in text
    assert "插件响应权限" not in text
    assert "轻量触发器" not in text


def test_ttd_help_admin_index_only_includes_admin_docs():
    text = format_help_index(section="admin")

    assert "ttd-bot 管理功能" in text
    assert "管理功能" in text
    assert "插件响应权限" in text
    assert "聊天统计" not in text
    assert "轻量触发器" not in text


def test_ttd_help_auto_index_only_includes_background_docs():
    text = format_help_index(section="background")

    assert "ttd-bot 自动触发 / 后台功能" in text
    assert "自动触发 / 后台功能" in text
    assert "轻量触发器" in text
    assert "聊天统计" not in text
    assert "插件响应权限" not in text


def test_ttd_help_detail_can_point_to_dependency_help_command():
    doc = get_feature_doc("deer")

    assert doc is not None
    text = format_doc_detail(doc)

    assert "鹿管签到" in text
    assert '更多用法见依赖插件内置帮助：发送 "🦌帮助" 或 "鹿帮助"。' in text


def test_ttd_help_lookup_accepts_provider_name():
    doc = get_feature_doc("nonebot_plugin_wordcloud")

    assert doc is not None
    assert doc.key == "wordcloud"


def test_ttd_help_long_message_threshold():
    assert _is_long_message("short") is False
    assert _is_long_message("x" * 1201) is True
    assert _is_long_message("\n".join(["x"] * 37)) is True

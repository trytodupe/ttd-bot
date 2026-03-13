import importlib
import json
import sys
from pathlib import Path

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

@pytest.fixture(scope="module")
def auto_ping_modules():
    try:
        driver = nonebot.get_driver()
    except ValueError:
        nonebot.init(superusers={"12345"})
        driver = nonebot.get_driver()

    from nonebot.adapters.onebot.v11 import Adapter

    try:
        driver.register_adapter(Adapter)
    except ValueError:
        pass

    nonebot.load_plugin("nonebot_plugin_localstore")
    nonebot.load_plugin("nonebot_plugin_uninfo")

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    package = importlib.import_module("auto_ping")
    storage = importlib.import_module("auto_ping.storage")
    helpers = importlib.import_module("auto_ping.helpers")
    return package, storage, helpers


def test_alias_registry_roundtrip(auto_ping_modules, tmp_path):
    _, storage, _ = auto_ping_modules
    registry = storage.AliasRegistry(tmp_path / "aliases.json")

    assert registry.all_targets() == {}

    registry.add_alias(123456, "Bob")
    registry.add_alias(123456, "b")
    registry.add_alias(234567, "alice")

    persisted = json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8"))
    assert persisted == {
        "targets": {
            "123456": ["b", "bob"],
            "234567": ["alice"],
        }
    }

    reloaded = storage.AliasRegistry(tmp_path / "aliases.json")
    assert reloaded.all_targets() == {
        123456: ("b", "bob"),
        234567: ("alice",),
    }


def test_alias_registry_rejects_casefold_conflict(auto_ping_modules, tmp_path):
    _, storage, _ = auto_ping_modules
    registry = storage.AliasRegistry(tmp_path / "aliases.json")

    registry.add_alias(123456, "Bob")

    with pytest.raises(storage.AliasConflictError):
        registry.add_alias(234567, "bob")


def test_alias_registry_remove_cleans_empty_target(auto_ping_modules, tmp_path):
    _, storage, _ = auto_ping_modules
    registry = storage.AliasRegistry(tmp_path / "aliases.json")

    registry.add_alias(123456, "bob")
    removed_qq = registry.remove_alias("BOB")

    assert removed_qq == 123456
    assert registry.all_targets() == {}
    assert json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8")) == {
        "targets": {}
    }


def test_match_targets_is_case_insensitive(auto_ping_modules, tmp_path):
    _, storage, _ = auto_ping_modules
    registry = storage.AliasRegistry(tmp_path / "aliases.json")

    registry.add_alias(123456, "Bob")
    registry.add_alias(234567, "alice")

    assert registry.match_targets("hello bob and ALICE and bob again") == {123456, 234567}


def test_parse_add_args_supports_group_at(auto_ping_modules):
    _, _, helpers = auto_ping_modules
    args = Message([MessageSegment.at(123456), MessageSegment.text(" bob")])

    parsed = helpers.parse_add_command_args(args, is_group=True)

    assert parsed.target_qq == 123456
    assert parsed.alias == "bob"


def test_parse_add_args_supports_group_qq(auto_ping_modules):
    _, _, helpers = auto_ping_modules

    parsed = helpers.parse_add_command_args(Message("123456 bob"), is_group=True)

    assert parsed.target_qq == 123456
    assert parsed.alias == "bob"


def test_parse_add_args_rejects_private_at(auto_ping_modules):
    _, _, helpers = auto_ping_modules
    args = Message([MessageSegment.at(123456), MessageSegment.text(" bob")])

    with pytest.raises(ValueError, match="Private chat does not support @user"):
        helpers.parse_add_command_args(args, is_group=False)


def test_parse_add_args_rejects_invalid_shapes(auto_ping_modules):
    _, _, helpers = auto_ping_modules

    with pytest.raises(ValueError, match="Usage: ttd ping add"):
        helpers.parse_add_command_args(Message("123456"), is_group=True)

    with pytest.raises(ValueError, match="Only one @user is allowed"):
        helpers.parse_add_command_args(
            Message([
                MessageSegment.at(123456),
                MessageSegment.at(234567),
                MessageSegment.text(" bob"),
            ]),
            is_group=True,
        )


def test_parse_remove_args_requires_single_alias(auto_ping_modules):
    _, _, helpers = auto_ping_modules

    assert helpers.parse_remove_command_args(Message("Bob")) == "bob"

    with pytest.raises(ValueError, match="Usage: ttd ping remove"):
        helpers.parse_remove_command_args(Message("bob extra"))


def test_display_name_and_visibility_helpers(auto_ping_modules):
    _, _, helpers = auto_ping_modules
    from nonebot_plugin_uninfo import Member, User

    member = Member(user=User(id="123456", name="alice"), nick="Alice")
    user = User(id="234567", name="bob")

    assert helpers.pick_display_name(member=member, qq=123456) == "Alice"
    assert helpers.pick_display_name(user=user, qq=234567) == "bob"
    assert helpers.pick_display_name(qq=345678) == "345678"
    assert helpers.visible_targets(
        {
            123456: ("alice",),
            234567: ("bob",),
        },
        {123456},
    ) == [(123456, ("alice",))]

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.plugin import get_plugin

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

    if get_plugin("nonebot_plugin_localstore") is None:
        nonebot.load_plugin("nonebot_plugin_localstore")
    if get_plugin("nonebot_plugin_uninfo") is None:
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


def test_parse_proposal_add_args_requires_group_at(auto_ping_modules):
    _, _, helpers = auto_ping_modules
    args = Message([MessageSegment.text("bob "), MessageSegment.at(123456)])

    parsed = helpers.parse_proposal_add_command_args(args)

    assert parsed.target_qq == 123456
    assert parsed.alias == "bob"

    with pytest.raises(ValueError, match="Usage: ttd ping add"):
        helpers.parse_proposal_add_command_args(Message("123456 bob"))


def test_parse_proposal_add_args_accepts_quoted_alias(auto_ping_modules):
    _, _, helpers = auto_ping_modules
    args = Message([MessageSegment.text("'the-trigger' "), MessageSegment.at(123456)])

    parsed = helpers.parse_proposal_add_command_args(args)

    assert parsed.alias == "the-trigger"


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


def test_proposal_store_roundtrip(auto_ping_modules, tmp_path):
    proposals = importlib.import_module("auto_ping.proposals")
    proposal = proposals.create_proposal(
        message_id=-740752214,
        group_id=1022240664,
        proposer_qq=1669790626,
        target_qq=123456,
        alias="Bob",
        approve_emoji_id="76",
        reject_emoji_id="424",
        created_at=123,
    )
    store = proposals.ProposalStore(tmp_path / "proposals.json")

    store.add(proposal)

    reloaded = proposals.ProposalStore(tmp_path / "proposals.json")
    assert reloaded.get(1022240664, -740752214) == proposal
    assert reloaded.find_by_alias("BOB") == proposal
    assert reloaded.remove(1022240664, -740752214) == proposal
    assert reloaded.all() == []


def test_parse_snowluma_reaction_notice(auto_ping_modules):
    proposals = importlib.import_module("auto_ping.proposals")
    event = SimpleNamespace(
        notice_type="group_msg_emoji_like",
        sub_type="add",
        group_id=1022240664,
        user_id=1669790626,
        message_id=-740752214,
        likes=[{"emoji_id": "76", "count": 2}],
    )

    notice = proposals.parse_reaction_notice(event)

    assert notice == proposals.ReactionNotice(
        message_id=-740752214,
        group_id=1022240664,
        user_id=1669790626,
        emoji_id="76",
        count=2,
        is_add=True,
    )


def test_reaction_approval_excludes_seed_and_accepts_superuser(auto_ping_modules, monkeypatch):
    package, _, _ = auto_ping_modules
    proposals = importlib.import_module("auto_ping.proposals")
    bot = SimpleNamespace(
        self_id="999",
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
        config=SimpleNamespace(superusers={"12345"}),
    )
    monkeypatch.setattr(
        package.config,
        "auto_ping_proposal_approval_threshold",
        3,
    )

    def notice(*, user_id: int, count: int, is_add: bool = True):
        return proposals.ReactionNotice(
            message_id=1,
            group_id=2,
            user_id=user_id,
            emoji_id="76",
            count=count,
            is_add=is_add,
        )

    assert package._should_approve_reaction(bot, notice(user_id=100, count=3)) is False
    assert package._should_approve_reaction(bot, notice(user_id=100, count=4)) is True
    assert package._should_approve_reaction(bot, notice(user_id=12345, count=2)) is True
    assert package._should_approve_reaction(bot, notice(user_id=999, count=4)) is False
    assert package._should_approve_reaction(bot, notice(user_id=100, count=4, is_add=False)) is False


def test_remove_alias_allows_owner_and_superuser_only(auto_ping_modules):
    package, _, _ = auto_ping_modules
    bot = SimpleNamespace(
        adapter=SimpleNamespace(get_name=lambda: "OneBot V11"),
        config=SimpleNamespace(superusers={"12345"}),
    )

    assert package._can_remove_alias(bot, requester_qq=100, owner_qq=100) is True
    assert package._can_remove_alias(bot, requester_qq=200, owner_qq=100) is False
    assert package._can_remove_alias(bot, requester_qq=12345, owner_qq=100) is True


def test_build_proposal_message_uses_matching_faces(auto_ping_modules):
    package, _, helpers = auto_ping_modules
    parsed = helpers.AddCommandArgs(target_qq=123456, alias="bob")

    message = package._build_proposal_message(parsed, 76, 424)

    assert [(segment.type, segment.data) for segment in message] == [
        ("text", {"text": "Proposal: bob -> "}),
        ("at", {"qq": "123456"}),
        ("text", {"text": "\n赞成："}),
        ("face", {"id": "76"}),
        ("text", {"text": " 反对："}),
        ("face", {"id": "424"}),
    ]


@pytest.mark.asyncio
async def test_create_alias_proposal_persists_and_adds_reactions(
    auto_ping_modules,
    tmp_path,
    monkeypatch,
):
    package, storage, helpers = auto_ping_modules
    proposals = importlib.import_module("auto_ping.proposals")
    registry = storage.AliasRegistry(tmp_path / "aliases.json")
    proposal_store = proposals.ProposalStore(tmp_path / "proposals.json")
    calls = []

    class FakeBot:
        async def call_api(self, api, **data):
            calls.append((api, data))
            if api == "send_group_msg":
                return {"message_id": -740752214}
            return None

    monkeypatch.setattr(package, "registry", registry)
    monkeypatch.setattr(package, "proposal_store", proposal_store)
    monkeypatch.setattr(package.random, "sample", lambda population, count: [76, 424])

    await package._create_alias_proposal(
        FakeBot(),
        SimpleNamespace(group_id=1022240664, user_id=1669790626),
        helpers.AddCommandArgs(target_qq=123456, alias="the-trigger"),
    )

    proposal = proposal_store.get(1022240664, -740752214)
    assert proposal is not None
    assert proposal.alias == "the-trigger"
    assert proposal.approve_emoji_id == "76"
    assert proposal.reject_emoji_id == "424"
    assert [api for api, _ in calls] == [
        "send_group_msg",
        "set_msg_emoji_like",
        "set_msg_emoji_like",
    ]
    assert calls[1][1]["emoji_id"] == "76"
    assert calls[2][1]["emoji_id"] == "424"


@pytest.mark.asyncio
async def test_approve_proposal_adds_alias_once(
    auto_ping_modules,
    tmp_path,
    monkeypatch,
):
    package, storage, _ = auto_ping_modules
    proposals = importlib.import_module("auto_ping.proposals")
    registry = storage.AliasRegistry(tmp_path / "aliases.json")
    proposal_store = proposals.ProposalStore(tmp_path / "proposals.json")
    proposal = proposals.create_proposal(
        message_id=100,
        group_id=200,
        proposer_qq=300,
        target_qq=400,
        alias="the-trigger",
        approve_emoji_id="76",
        reject_emoji_id="424",
    )
    proposal_store.add(proposal)
    calls = []

    class FakeBot:
        async def call_api(self, api, **data):
            calls.append((api, data))
            return {"message_id": 101}

    monkeypatch.setattr(package, "registry", registry)
    monkeypatch.setattr(package, "proposal_store", proposal_store)

    assert await package._approve_proposal(FakeBot(), proposal) is True
    assert await package._approve_proposal(FakeBot(), proposal) is False

    assert registry.get_alias_owner("the-trigger") == 400
    assert proposal_store.all() == []
    assert [api for api, _ in calls] == ["send_group_msg"]

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest


@pytest.fixture(scope="module")
def quickmatch_query_module():
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

    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins"
    plugin_dir_text = str(plugin_dir)
    if plugin_dir_text not in sys.path:
        sys.path.insert(0, plugin_dir_text)

    module_name = "quickmatch_query"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    return module


def test_extract_username(quickmatch_query_module):
    module = quickmatch_query_module

    assert module._extract_username("qm trytodupe") == "trytodupe"
    assert module._extract_username("quickmatch The Kush Van Man") == "The Kush Van Man"
    assert module._extract_username("QM user_name-[x]") == "user_name-[x]"
    assert module._extract_username(" qm   Toy ") == "Toy"
    assert module._extract_username("/qm Toy") is None
    assert module._extract_username("qm") is None
    assert module._extract_username("qm bad/name") is None


def test_format_quickmatch_message(quickmatch_query_module):
    module = quickmatch_query_module

    message = module._format_quickmatch_message(
        "[SHK]trytodupe",
        "32145451",
        [
            {
                "first_placements": 5,
                "is_rating_provisional": False,
                "plays": 10,
                "pool_id": 8,
                "pool": {
                    "active": True,
                    "id": 8,
                    "name": "RP: Season 0",
                    "ruleset_id": 0,
                    "variant_id": 0,
                },
                "rank": 5732,
                "rating": 1565,
                "total_points": 0,
            },
            {
                "first_placements": 0,
                "is_rating_provisional": True,
                "plays": 0,
                "pool_id": 7,
                "pool": {
                    "active": True,
                    "id": 7,
                    "name": "QP 1v1",
                    "ruleset_id": 0,
                    "variant_id": 0,
                },
                "rank": 3190,
                "rating": 1521,
                "total_points": 0,
            },
        ],
    )

    assert message == (
        "[SHK]trytodupe / 32145451\n"
        "Quickmatch: #3,190\n"
        "RP: Season 0 | Rank #5,732 | Wins 5 | Plays 10 | Points 0 | Rating 1,565\n"
        "QP 1v1 | Rank #3,190 | Wins 0 | Plays 0 | Points 0 | Rating 1,521*"
    )


def test_format_pool_display_name_with_variant(quickmatch_query_module):
    module = quickmatch_query_module

    assert (
        module._format_pool_display_name(
            {
                "active": True,
                "id": 1,
                "name": "Ladder",
                "ruleset_id": 3,
                "variant_id": 4,
            },
            1,
        )
        == "[4k] Ladder"
    )


@pytest.mark.asyncio
async def test_handle_quickmatch_query_success(quickmatch_query_module, monkeypatch):
    module = quickmatch_query_module
    captured = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_lookup_user(client, username):
        assert username == "trytodupe"
        return ("32145451", "[SHK]trytodupe")

    async def fake_fetch_quickmatch_stats(client, user_id):
        assert user_id == "32145451"
        return (
            "[SHK]trytodupe",
            [
                {
                    "first_placements": 5,
                    "is_rating_provisional": False,
                    "plays": 10,
                    "pool_id": 8,
                    "pool": {
                        "active": True,
                        "id": 8,
                        "name": "RP: Season 0",
                        "ruleset_id": 0,
                        "variant_id": 0,
                    },
                    "rank": 5732,
                    "rating": 1565,
                    "total_points": 0,
                }
            ],
        )

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user", fake_lookup_user)
    monkeypatch.setattr(module, "_fetch_quickmatch_stats", fake_fetch_quickmatch_stats)

    event = SimpleNamespace(get_plaintext=lambda: "qm trytodupe")
    await module.handle_quickmatch_query(event)

    assert captured["message"] == (
        "[SHK]trytodupe / 32145451\n"
        "Quickmatch: #5,732\n"
        "RP: Season 0 | Rank #5,732 | Wins 5 | Plays 10 | Points 0 | Rating 1,565"
    )


@pytest.mark.asyncio
async def test_handle_quickmatch_query_no_stats(quickmatch_query_module, monkeypatch):
    module = quickmatch_query_module
    captured = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_lookup_user(client, username):
        return ("32145451", "trytodupe")

    async def fake_fetch_quickmatch_stats(client, user_id):
        return ("trytodupe", [])

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user", fake_lookup_user)
    monkeypatch.setattr(module, "_fetch_quickmatch_stats", fake_fetch_quickmatch_stats)

    event = SimpleNamespace(get_plaintext=lambda: "quickmatch trytodupe")
    await module.handle_quickmatch_query(event)

    assert captured == {"message": "No quickmatch stats: trytodupe"}


@pytest.mark.asyncio
async def test_handle_quickmatch_query_user_not_found(quickmatch_query_module, monkeypatch):
    module = quickmatch_query_module
    captured = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_lookup_user(client, username):
        return None

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user", fake_lookup_user)

    event = SimpleNamespace(get_plaintext=lambda: "qm dd")
    await module.handle_quickmatch_query(event)

    assert captured == {"message": "User not found: dd"}

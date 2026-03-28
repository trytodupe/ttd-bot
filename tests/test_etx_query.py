import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest


@pytest.fixture(scope="module")
def etx_query_module():
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

    module_name = "etx_query"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    return module


def test_extract_username(etx_query_module):
    module = etx_query_module

    assert module._extract_username("etx trytodupe") == "trytodupe"
    assert module._extract_username("ETX The Kush Van Man") == "The Kush Van Man"
    assert module._extract_username(" etx   Toy ") == "Toy"
    assert module._extract_username("/etx Toy") is None
    assert module._extract_username("etx") is None


def test_extract_user_id_from_location(etx_query_module):
    module = etx_query_module

    assert module._extract_user_id_from_location("https://osu.ppy.sh/users/15416101") == "15416101"
    assert module._extract_user_id_from_location("/users/15416101?mode=osu") == "15416101"
    assert module._extract_user_id_from_location("https://osu.ppy.sh/home") is None


def test_format_duel_rating_message(etx_query_module):
    module = etx_query_module

    payload = {
        "osuUserId": "15416101",
        "duelRating": {
            "osuNoModDuelStarRating": "4.97233790756486",
            "osuHiddenDuelStarRating": "5.41597135939341",
            "osuHardRockDuelStarRating": "4.59178657368894",
            "osuDoubleTimeDuelStarRating": "5.43107259066218",
            "osuFreeModDuelStarRating": "5.36273102733878",
            "osuDuelOutdated": True,
            "updatedAt": "2026-03-27T11:51:37.360Z",
        },
    }

    message = module._format_duel_rating_message("trytodupe", payload)

    assert message == (
        "trytodupe / 15416101:\n"
        "NM: 4.972\n"
        "HD: 5.416\n"
        "HR: 4.592\n"
        "DT: 5.431\n"
        "FM: 5.363\n"
        "updated at 2026-03-27 19:51:37 (OUTDATED)"
    )


@pytest.mark.asyncio
async def test_handle_etx_query_success(etx_query_module, monkeypatch):
    module = etx_query_module
    captured = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_lookup_user_id(client, username):
        assert username == "trytodupe"
        return "15416101"

    async def fake_fetch_duel_rating(client, user_id):
        assert user_id == "15416101"
        return {
            "osuUserId": "15416101",
            "duelRating": {
                "osuNoModDuelStarRating": "4.97233790756486",
                "osuHiddenDuelStarRating": "5.41597135939341",
                "osuHardRockDuelStarRating": "4.59178657368894",
                "osuDoubleTimeDuelStarRating": "5.43107259066218",
                "osuFreeModDuelStarRating": "5.36273102733878",
                "osuDuelOutdated": False,
                "updatedAt": "2026-03-27T11:51:37.360Z",
            },
        }

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user_id", fake_lookup_user_id)
    monkeypatch.setattr(module, "_fetch_duel_rating", fake_fetch_duel_rating)

    event = SimpleNamespace(get_plaintext=lambda: "etx trytodupe")
    await module.handle_etx_query(event)

    assert captured["message"].startswith("trytodupe / 15416101:\n")
    assert "DT: 5.431" in captured["message"]


@pytest.mark.asyncio
async def test_handle_etx_query_user_not_found(etx_query_module, monkeypatch):
    module = etx_query_module
    captured = {}

    async def fake_finish(message=None, **kwargs):
        captured["message"] = message

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_lookup_user_id(client, username):
        return None

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user_id", fake_lookup_user_id)

    event = SimpleNamespace(get_plaintext=lambda: "etx unknown user")
    await module.handle_etx_query(event)

    assert captured == {"message": "User not found: unknown user"}

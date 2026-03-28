import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
    assert module._extract_username("etx user_name-[x]") == "user_name-[x]"
    assert module._extract_username(" etx   Toy ") == "Toy"
    assert module._extract_username("/etx Toy") is None
    assert module._extract_username("etx") is None
    assert module._extract_username("etx bad/name") is None
    assert module._extract_username("etx bad@name") is None


def test_extract_user_id_from_location(etx_query_module):
    module = etx_query_module

    assert module._extract_user_id_from_location("https://osu.ppy.sh/users/15416101") == "15416101"
    assert module._extract_user_id_from_location("/users/15416101?mode=osu") == "15416101"
    assert module._extract_user_id_from_location("https://osu.ppy.sh/home") is None


def test_has_osu_oauth_config(etx_query_module, monkeypatch):
    module = etx_query_module

    monkeypatch.setattr(module.plugin_config, "etx_osu_client_id", "50302", raising=False)
    monkeypatch.setattr(module.plugin_config, "etx_osu_client_secret", "secret", raising=False)
    assert module._has_osu_oauth_config() is True

    monkeypatch.setattr(module.plugin_config, "etx_osu_client_secret", "", raising=False)
    assert module._has_osu_oauth_config() is False


def test_format_duel_rating_message(etx_query_module):
    module = etx_query_module
    now = datetime(2026, 3, 28, 20, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    payload = {
        "osuUserId": "15416101",
        "duelRating": {
            "osuDuelStarRating": "5.1643987573369",
            "osuNoModDuelStarRating": "4.97233790756486",
            "osuHiddenDuelStarRating": "5.41597135939341",
            "osuHardRockDuelStarRating": "4.59178657368894",
            "osuDoubleTimeDuelStarRating": "5.43107259066218",
            "osuFreeModDuelStarRating": "5.36273102733878",
            "updatedAt": "2026-03-27T11:51:37.360Z",
        },
    }

    message = module._format_duel_rating_message("trytodupe", payload, now=now)

    assert message == (
        "trytodupe / 15416101:\n"
        "SR: 5.164\n"
        "NM: 4.972\n"
        "HD: 5.416\n"
        "HR: 4.592\n"
        "DT: 5.431\n"
        "FM: 5.363\n"
        "updated at 2026-03-27 19:51:37 (~1d)"
    )


def test_format_relative_age(etx_query_module):
    module = etx_query_module
    now = datetime(2026, 3, 28, 20, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert (
        module._format_relative_age(
            datetime(2026, 3, 28, 19, 59, 50, tzinfo=ZoneInfo("Asia/Shanghai")),
            now=now,
        )
        == "~1m"
    )
    assert (
        module._format_relative_age(
            datetime(2026, 3, 28, 19, 28, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            now=now,
        )
        == "~32m"
    )
    assert (
        module._format_relative_age(
            datetime(2026, 3, 28, 17, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            now=now,
        )
        == "~3h"
    )
    assert (
        module._format_relative_age(
            datetime(2026, 3, 27, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            now=now,
        )
        == "~1d"
    )
    assert (
        module._format_relative_age(
            datetime(2026, 1, 20, 20, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            now=now,
        )
        == "~2mo"
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

    async def fake_lookup_user(client, username):
        assert username == "trytodupe"
        return ("15416101", "[SHK]trytodupe")

    async def fake_fetch_duel_rating(client, user_id):
        assert user_id == "15416101"
        return {
            "osuUserId": "15416101",
            "duelRating": {
                "osuDuelStarRating": "5.1643987573369",
                "osuNoModDuelStarRating": "4.97233790756486",
                "osuHiddenDuelStarRating": "5.41597135939341",
                "osuHardRockDuelStarRating": "4.59178657368894",
                "osuDoubleTimeDuelStarRating": "5.43107259066218",
                "osuFreeModDuelStarRating": "5.36273102733878",
                "updatedAt": "2026-03-27T11:51:37.360Z",
            },
        }

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user", fake_lookup_user)
    monkeypatch.setattr(module, "_fetch_duel_rating", fake_fetch_duel_rating)

    event = SimpleNamespace(get_plaintext=lambda: "etx trytodupe")
    await module.handle_etx_query(event)

    assert captured["message"].startswith("[SHK]trytodupe / 15416101:\n")
    assert "SR: 5.164" in captured["message"]
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

    async def fake_lookup_user(client, username):
        return None

    monkeypatch.setattr(module.matcher, "finish", fake_finish)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient())
    monkeypatch.setattr(module, "_lookup_user", fake_lookup_user)

    event = SimpleNamespace(get_plaintext=lambda: "etx unknown user")
    await module.handle_etx_query(event)

    assert captured == {"message": "User not found: unknown user"}


@pytest.mark.asyncio
async def test_lookup_user_falls_back_to_redirect(etx_query_module, monkeypatch):
    module = etx_query_module

    monkeypatch.setattr(module.plugin_config, "etx_osu_client_id", "50302", raising=False)
    monkeypatch.setattr(module.plugin_config, "etx_osu_client_secret", "secret", raising=False)

    async def fake_lookup_user_by_api(client, username):
        raise RuntimeError("oauth failed")

    async def fake_lookup_user_by_redirect(client, username):
        return ("15416101", username)

    monkeypatch.setattr(module, "_lookup_user_by_api", fake_lookup_user_by_api)
    monkeypatch.setattr(module, "_lookup_user_by_redirect", fake_lookup_user_by_redirect)

    result = await module._lookup_user(object(), "trytodupe")

    assert result == ("15416101", "trytodupe")

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest


@pytest.fixture(scope="module")
def release_note_module():
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

    module_name = "release_note"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    module._ALERT_KEYS_SENT.clear()
    return module


class FakeBot:
    def __init__(self):
        self.calls = []

    async def call_api(self, api: str, **kwargs):
        self.calls.append((api, kwargs))
        return {"status": "ok"}


def test_resolve_primary_superuser_prefers_env_order(release_note_module, monkeypatch):
    monkeypatch.setenv("SUPERUSERS", '["1669790626", "1777777777"]')
    monkeypatch.setattr(
        release_note_module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers={"9999999999"})),
    )

    assert release_note_module._resolve_primary_superuser() == 1669790626


def test_resolve_primary_superuser_fallback_driver(release_note_module, monkeypatch):
    monkeypatch.delenv("SUPERUSERS", raising=False)
    monkeypatch.setattr(
        release_note_module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers={"1669790627", "1669790628"})),
    )

    assert release_note_module._resolve_primary_superuser() == 1669790627


@pytest.mark.asyncio
async def test_notify_github_auth_failure_sends_once(release_note_module, monkeypatch):
    fake_bot = FakeBot()
    release_note_module._ALERT_KEYS_SENT.clear()

    monkeypatch.setenv("SUPERUSERS", '["1669790626"]')
    monkeypatch.setattr(
        release_note_module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers=set())),
    )
    monkeypatch.setattr(release_note_module, "get_bots", lambda: {"bot": fake_bot})

    await release_note_module._notify_github_auth_failure(
        operation="get_tag_commit_sha",
        status_code=401,
        body_text="Bad credentials",
    )
    await release_note_module._notify_github_auth_failure(
        operation="update_tag:create",
        status_code=401,
        body_text="Bad credentials",
    )

    assert len(fake_bot.calls) == 1
    api, payload = fake_bot.calls[0]
    assert api == "send_private_msg"
    assert payload["user_id"] == 1669790626
    assert "GitHub token/auth failure (401)" in payload["message"]


@pytest.mark.asyncio
async def test_notify_github_auth_failure_ignores_non_auth(release_note_module, monkeypatch):
    fake_bot = FakeBot()
    release_note_module._ALERT_KEYS_SENT.clear()

    monkeypatch.setenv("SUPERUSERS", '["1669790626"]')
    monkeypatch.setattr(
        release_note_module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers=set())),
    )
    monkeypatch.setattr(release_note_module, "get_bots", lambda: {"bot": fake_bot})

    await release_note_module._notify_github_auth_failure(
        operation="get_commits_between",
        status_code=404,
        body_text="Bad credentials",
    )
    await release_note_module._notify_github_auth_failure(
        operation="get_commits_between",
        status_code=500,
        body_text="Internal Server Error",
    )

    assert fake_bot.calls == []


@pytest.mark.asyncio
async def test_send_private_alert_once_without_bot(release_note_module, monkeypatch):
    release_note_module._ALERT_KEYS_SENT.clear()

    monkeypatch.setenv("SUPERUSERS", '["1669790626"]')
    monkeypatch.setattr(
        release_note_module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers=set())),
    )
    monkeypatch.setattr(release_note_module, "get_bots", lambda: {})

    result = await release_note_module._send_private_alert_once("github-auth-invalid", "test")

    assert result is False

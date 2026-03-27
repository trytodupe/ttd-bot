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


@pytest.mark.asyncio
async def test_publish_release_note_uses_call_api(release_note_module, monkeypatch):
    fake_bot = FakeBot()
    monkeypatch.setattr(release_note_module, "get_bots", lambda: {"bot": fake_bot})

    result = await release_note_module.publish_release_note("hello")

    assert result is True
    assert fake_bot.calls == [("set_self_longnick", {"longNick": "hello"})]


@pytest.mark.asyncio
async def test_publish_release_note_treats_failed_retcode_as_failure(
    release_note_module, monkeypatch
):
    class FailedBot(FakeBot):
        async def call_api(self, api: str, **kwargs):
            self.calls.append((api, kwargs))
            return {"status": "failed", "retcode": 403, "message": "Forbidden"}

    fake_bot = FailedBot()
    monkeypatch.setattr(release_note_module, "get_bots", lambda: {"bot": fake_bot})

    result = await release_note_module.publish_release_note("hello")

    assert result is False


@pytest.mark.asyncio
async def test_check_and_publish_release_note_updates_tag_even_if_publish_fails(
    release_note_module, monkeypatch
):
    update_calls = []

    async def fake_get_current_version():
        return "v1.3.11"

    async def fake_get_tag_commit_sha(tag_name: str):
        if tag_name == "v1.3.11":
            return "current-sha"
        if tag_name == release_note_module.LAST_DEPLOYED_TAG:
            return "last-sha"
        return None

    async def fake_get_commits_between(base_sha, head_sha):
        return [{"commit": {"message": "feat: example"}}]

    async def fake_get_version_tags_at_commit(commit_sha: str):
        return ["v1.3.10"]

    async def fake_publish_release_note(_release_note: str):
        return False

    async def fake_get_github_token():
        return "token"

    async def fake_update_tag(tag_name: str, commit_sha: str, token: str):
        update_calls.append((tag_name, commit_sha, token))
        return True

    monkeypatch.setattr(release_note_module, "get_current_version", fake_get_current_version)
    monkeypatch.setattr(release_note_module, "get_tag_commit_sha", fake_get_tag_commit_sha)
    monkeypatch.setattr(release_note_module, "get_commits_between", fake_get_commits_between)
    monkeypatch.setattr(
        release_note_module,
        "get_version_tags_at_commit",
        fake_get_version_tags_at_commit,
    )
    monkeypatch.setattr(release_note_module, "publish_release_note", fake_publish_release_note)
    monkeypatch.setattr(release_note_module, "get_github_token", fake_get_github_token)
    monkeypatch.setattr(release_note_module, "update_tag", fake_update_tag)

    await release_note_module.check_and_publish_release_note()

    assert update_calls == [("last-deployed", "current-sha", "token")]

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import nonebot
import pytest

# ---------------------------------------------------------------------------
# helpers for testing APK sources directly (no NoneBot dependency)
# ---------------------------------------------------------------------------


def _import_sources():
    """Import sources.py directly without triggering __init__.py's NoneBot init."""
    import importlib

    sources_dir = (
        Path(__file__).resolve().parents[1]
        / "src" / "plugins" / "coc_apk_checker"
    )
    sources_dir_text = str(sources_dir)
    if sources_dir_text not in sys.path:
        sys.path.insert(0, sources_dir_text)

    if "sources" in sys.modules:
        return importlib.reload(sys.modules["sources"])
    return importlib.import_module("sources")


# ---------------------------------------------------------------------------
# module fixture for tests that need the full NoneBot plugin
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def coc_apk_checker_module():
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

    module_name = "coc_apk_checker"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# ApkPureSource tests (direct, no NoneBot)
# ---------------------------------------------------------------------------


def test_select_latest_version_filters_only_apk():
    sources = _import_sources()
    source = sources.ApkPureSource()

    payload = {
        "version_list": [
            {
                "version_name": "18.200.19",
                "version_code": "180200020",
                "update_date": "2026-03-20T11:44:56+07:00",
                "asset": {"type": "APK"},
            },
            {
                "version_name": "18.100.10",
                "version_code": "180100010",
                "update_date": "2026-02-01T00:00:00+07:00",
                "asset": {"type": "APK"},
            },
            {
                "version_name": "18.200.19",
                "version_code": "180200020",
                "update_date": "2026-03-20T11:44:56+07:00",
                "asset": {"type": "XAPK"},
            },
        ]
    }

    latest = source._select_latest_version(payload)

    assert latest == sources.CocVersion(
        version_name="18.200.19",
        version_code="180200020",
        update_date="2026-03-20T11:44:56+07:00",
    )


def test_decode_content_disposition_filename():
    sources = _import_sources()
    source = sources.ApkPureSource()

    header = 'attachment; filename="Clash of Clans_18.200.19_APKPure.apk"'
    assert (
        source._decode_content_disposition_filename(header)
        == "Clash_of_Clans_18.200.19_APKPure.apk"
    )
    plus_header = 'attachment; filename="Clash+of+Clans_18.367.1_APKPure.apk"'
    assert (
        source._decode_content_disposition_filename(plus_header)
        == "Clash_of_Clans_18.367.1_APKPure.apk"
    )


def test_is_expected_apk_content_type_accepts_vendor_and_generic_binary():
    sources = _import_sources()
    source = sources.ApkPureSource()

    assert source._is_expected_apk_content_type(
        "application/vnd.android.package-archive"
    )
    assert source._is_expected_apk_content_type("application/octet-stream")
    assert source._is_expected_apk_content_type(
        "application/octet-stream; charset=binary"
    )
    assert not source._is_expected_apk_content_type("text/html")


def test_looks_like_zip_archive():
    sources = _import_sources()

    assert sources._looks_like_zip_archive(b"PK\x03\x04rest")
    assert not sources._looks_like_zip_archive(b"not-zip")


# ---------------------------------------------------------------------------
# factory tests
# ---------------------------------------------------------------------------


def test_create_source_returns_correct_type():
    sources = _import_sources()

    s = sources.create_source("apkcombo")
    assert isinstance(s, sources.ApkComboSource)
    assert s.SOURCE_NAME == "apkcombo"

    s2 = sources.create_source("apkpure")
    assert isinstance(s2, sources.ApkPureSource)
    assert s2.SOURCE_NAME == "apkpure"

    with pytest.raises(ValueError, match="Unknown APK source"):
        sources.create_source("invalid")


# ---------------------------------------------------------------------------
# __init__.py tests (need full NoneBot module)
# ---------------------------------------------------------------------------


def test_extract_version_name_from_filename(coc_apk_checker_module):
    module = coc_apk_checker_module

    assert (
        module._extract_version_name_from_filename(
            "Clash of Clans_18.200.19_APKPure.apk"
        )
        == "18.200.19"
    )
    assert (
        module._extract_version_name_from_filename(
            "Clash_of_Clans_18.200.19_APKPure.apk"
        )
        == "18.200.19"
    )
    assert (
        module._extract_version_name_from_filename(
            "Clash+of+Clans_18.367.1_APKPure.apk"
        )
        == "18.367.1"
    )
    # Also works with APKCombo naming
    assert (
        module._extract_version_name_from_filename(
            "Clash of Clans_18.400.2_apkcombo.com.apk"
        )
        == "18.400.2"
    )
    assert module._extract_version_name_from_filename("other.apk") is None


def test_extract_upload_error(coc_apk_checker_module):
    module = coc_apk_checker_module

    assert (
        module._extract_upload_error(
            {
                "status": "failed",
                "retcode": 200,
                "wording": "ENOENT: no such file or directory",
            }
        )
        == "ENOENT: no such file or directory"
    )
    assert module._extract_upload_error({"status": "ok", "retcode": 0}) == ""
    assert (
        module._extract_upload_error(
            {"file_id": "/10584a2b-9b86-4777-aefa-19655cfee558"}
        )
        == ""
    )
    assert (
        module._extract_upload_error(
            {"data": {"file_id": "/78458762-7bf5-4eed-9e2c-30e223e108c9"}}
        )
        == ""
    )


def test_resolve_primary_superuser_prefers_env_order(
    coc_apk_checker_module, monkeypatch
):
    module = coc_apk_checker_module

    monkeypatch.setenv("SUPERUSERS", '["1669790626", "1777777777"]')
    monkeypatch.setattr(
        module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers={"9999999999"})),
    )

    assert module._resolve_primary_superuser() == 1669790626


def test_build_http_client_uses_configured_proxy(coc_apk_checker_module, monkeypatch):
    module = coc_apk_checker_module
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_timeout_seconds",
        120,
        raising=False,
    )
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_proxy",
        "http://127.0.0.1:7890",
        raising=False,
    )

    module._build_http_client()

    assert captured["proxy"] == "http://127.0.0.1:7890"
    assert captured["trust_env"] is True


# ---------------------------------------------------------------------------
# check_coc_apk_update tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_coc_apk_update_retries_upload_when_latest_file_exists(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / "Clash of Clans_18.200.19_APKPure.apk").write_bytes(b"apk")

    sent_messages = []
    download_calls = []
    upload_calls = []

    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_group_id",
        607572668,
        raising=False,
    )

    async def fake_get_latest_version(_client):
        return module.CocVersion(
            version_name="18.200.19",
            version_code="180200020",
            update_date="2026-03-20T11:44:56+07:00",
        )

    async def fake_download_apk(_client, _shared_dir):
        download_calls.append(True)
        return module.DownloadedApk(
            filename="Clash_of_Clans_18.200.19_APKPure.apk",
            path=shared_dir / "Clash_of_Clans_18.200.19_APKPure.apk",
        )

    async def fake_upload_group_file(_group_id, _apk):
        upload_calls.append(True)
        return module.UploadResult(status=module.UploadStatus.CONFIRMED)

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )
    monkeypatch.setattr(module._APK_SOURCE, "download_apk", fake_download_apk)
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)

    await module.check_coc_apk_update()

    assert sent_messages == []
    assert download_calls == []
    assert upload_calls == [True]
    assert "18.200.19" in module._uploaded_versions


@pytest.mark.asyncio
async def test_check_coc_apk_update_retries_upload_when_plus_named_file_exists(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / "Clash+of+Clans_18.367.1_APKPure.apk").write_bytes(b"apk")

    sent_messages = []
    download_calls = []
    upload_calls = []

    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_group_id",
        607572668,
        raising=False,
    )

    async def fake_get_latest_version(_client):
        return module.CocVersion(
            version_name="18.367.1",
            version_code="180367002",
            update_date="2026-05-26T08:12:10+07:00",
        )

    async def fake_download_apk(_client, _shared_dir):
        download_calls.append(True)
        return module.DownloadedApk(
            filename="Clash_of_Clans_18.367.1_APKPure.apk",
            path=shared_dir / "Clash_of_Clans_18.367.1_APKPure.apk",
        )

    async def fake_upload_group_file(_group_id, _apk):
        upload_calls.append(True)
        return module.UploadResult(status=module.UploadStatus.CONFIRMED)

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )
    monkeypatch.setattr(module._APK_SOURCE, "download_apk", fake_download_apk)
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)

    await module.check_coc_apk_update()

    assert sent_messages == []
    assert download_calls == []
    assert upload_calls == [True]
    assert "18.367.1" in module._uploaded_versions


@pytest.mark.asyncio
async def test_check_coc_apk_update_sends_version_message_and_uploads(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()

    sent_messages = []
    uploaded = []

    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_group_id",
        607572668,
        raising=False,
    )

    version = module.CocVersion(
        version_name="18.200.19",
        version_code="180200020",
        update_date="2026-03-20T11:44:56+07:00",
    )
    downloaded = module.DownloadedApk(
        filename="Clash_of_Clans_18.200.19_APKPure.apk",
        path=shared_dir / "Clash_of_Clans_18.200.19_APKPure.apk",
    )

    async def fake_get_latest_version(_client):
        return version

    async def fake_download_apk(_client, _shared_dir):
        downloaded.path.write_bytes(b"apk")
        return downloaded

    async def fake_upload_group_file(group_id, apk):
        uploaded.append((group_id, apk))
        return module.UploadResult(status=module.UploadStatus.CONFIRMED)

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )
    monkeypatch.setattr(module._APK_SOURCE, "download_apk", fake_download_apk)
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)

    await module.check_coc_apk_update()

    assert sent_messages == [
        (
            607572668,
            "[CoC APK] New version detected\n"
            "version_name: 18.200.19\n"
            "version_code: 180200020\n"
            "update_date: 2026-03-20T11:44:56+07:00",
        )
    ]
    assert uploaded == [(607572668, downloaded)]


@pytest.mark.asyncio
async def test_check_coc_apk_update_does_not_persist_unknown_upload(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    version_name = "99.1.2"
    apk_path = shared_dir / f"Clash_of_Clans_{version_name}_APKCombo.apk"
    apk_path.write_bytes(b"apk")

    module._uploaded_versions.discard(version_name)
    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_group_id",
        607572668,
        raising=False,
    )

    async def fake_get_latest_version(_client):
        return module.CocVersion(
            version_name=version_name,
            version_code="99001002",
            update_date="2026-08-11T05:54:25Z",
        )

    async def fake_upload_group_file(_group_id, _apk):
        return module.UploadResult(
            status=module.UploadStatus.UNKNOWN,
            detail="upload outcome could not be confirmed",
        )

    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)

    await module.check_coc_apk_update()

    assert version_name not in module._uploaded_versions
    assert not (shared_dir / module._UPLOADED_MARKER).exists()


@pytest.mark.asyncio
async def test_upload_group_file_confirms_from_message_sent(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)
    upload_started = asyncio.Event()

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            if api == "get_group_root_files":
                return {"files": []}
            if api == "upload_group_file":
                upload_started.set()
                await asyncio.Future()
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    upload_task = asyncio.create_task(module._upload_group_file(607572668, apk))
    await asyncio.wait_for(upload_started.wait(), timeout=1)
    event = module.Event.model_validate(
        {
            "time": 1786442842,
            "post_type": "message_sent",
            "message_type": "group",
            "group_id": 607572668,
            "self_id": 1940196378,
            "user_id": 1940196378,
            "message": [
                {
                    "type": "file",
                    "data": {
                        "file_id": "/confirmed-by-event",
                        "name": f"{apk.filename}.1",
                        "size": apk_path.stat().st_size,
                    },
                }
            ],
        }
    )
    module._handle_message_sent_event(event)

    result = await asyncio.wait_for(upload_task, timeout=1)

    assert result.status is module.UploadStatus.CONFIRMED
    assert result.file_id == "/confirmed-by-event"


@pytest.mark.asyncio
async def test_message_sent_does_not_confirm_another_users_file(
    coc_apk_checker_module, monkeypatch
):
    module = coc_apk_checker_module
    receipt = asyncio.get_running_loop().create_future()
    pending = module.PendingUpload(
        group_id=607572668,
        filename="Clash_of_Clans_99.1.2_APKCombo.apk",
        file_size=3,
        receipt=receipt,
    )
    monkeypatch.setattr(module, "_pending_upload", pending)
    event = module.Event.model_validate(
        {
            "time": 1786442842,
            "post_type": "message_sent",
            "message_type": "group",
            "group_id": 607572668,
            "self_id": 1940196378,
            "user_id": 123456789,
            "message": [
                {
                    "type": "file",
                    "data": {
                        "file_id": "/another-users-file",
                        "name": f"{pending.filename}.1",
                        "size": pending.file_size,
                    },
                }
            ],
        }
    )

    module._handle_message_sent_event(event)

    assert not receipt.done()


@pytest.mark.asyncio
async def test_upload_group_file_confirms_from_api_file_id(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            if api == "get_group_root_files":
                return {"files": []}
            if api == "upload_group_file":
                return {"file_id": "/confirmed-by-api"}
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    result = await module._upload_group_file(607572668, apk)

    assert result.status is module.UploadStatus.CONFIRMED
    assert result.file_id == "/confirmed-by-api"


@pytest.mark.asyncio
async def test_upload_group_file_reconciles_timeout_with_group_files(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)
    list_calls = 0

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            nonlocal list_calls
            if api == "get_group_root_files":
                list_calls += 1
                if list_calls == 1:
                    return {"files": []}
                return {
                    "files": [
                        {
                            "file_id": "/confirmed-by-list",
                            "file_name": f"{apk.filename}.1",
                            "file_size": apk_path.stat().st_size,
                            "uploader": int(self.self_id),
                        }
                    ]
                }
            if api == "upload_group_file":
                raise TimeoutError("WebSocket call api upload_group_file timeout")
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    result = await module._upload_group_file(607572668, apk)

    assert result.status is module.UploadStatus.CONFIRMED
    assert result.file_id == "/confirmed-by-list"
    assert list_calls == 2


@pytest.mark.asyncio
async def test_upload_group_file_accepts_message_sent_during_reconciliation(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)
    reconcile_started = asyncio.Event()
    finish_reconcile = asyncio.Event()
    list_calls = 0

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            nonlocal list_calls
            if api == "get_group_root_files":
                list_calls += 1
                if list_calls == 2:
                    reconcile_started.set()
                    await finish_reconcile.wait()
                return {"files": []}
            if api == "upload_group_file":
                raise TimeoutError("WebSocket call api upload_group_file timeout")
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    upload_task = asyncio.create_task(module._upload_group_file(607572668, apk))
    await asyncio.wait_for(reconcile_started.wait(), timeout=1)
    event = module.Event.model_validate(
        {
            "time": 1786442842,
            "post_type": "message_sent",
            "message_type": "group",
            "group_id": 607572668,
            "self_id": 1940196378,
            "user_id": 1940196378,
            "message": [
                {
                    "type": "file",
                    "data": {
                        "file_id": "/confirmed-during-reconcile",
                        "name": f"{apk.filename}.1",
                        "size": apk_path.stat().st_size,
                    },
                }
            ],
        }
    )
    module._handle_message_sent_event(event)
    finish_reconcile.set()

    result = await asyncio.wait_for(upload_task, timeout=1)

    assert result.status is module.UploadStatus.CONFIRMED
    assert result.file_id == "/confirmed-during-reconcile"


@pytest.mark.asyncio
async def test_upload_group_file_keeps_absent_timeout_unknown(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            if api == "get_group_root_files":
                return {"files": []}
            if api == "upload_group_file":
                raise TimeoutError("WebSocket call api upload_group_file timeout")
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    result = await module._upload_group_file(607572668, apk)

    assert result.status is module.UploadStatus.UNKNOWN
    assert result.file_id is None


@pytest.mark.asyncio
async def test_upload_group_file_does_not_reupload_existing_group_file(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)
    upload_calls = 0

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            nonlocal upload_calls
            if api == "get_group_root_files":
                return {
                    "files": [
                        {
                            "file_id": "/already-uploaded",
                            "file_name": f"{apk.filename}.1",
                            "file_size": apk_path.stat().st_size,
                            "uploader": int(self.self_id),
                        }
                    ]
                }
            if api == "upload_group_file":
                upload_calls += 1
                return {"file_id": "/duplicate"}
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    result = await module._upload_group_file(607572668, apk)

    assert result.status is module.UploadStatus.CONFIRMED
    assert result.file_id == "/already-uploaded"
    assert upload_calls == 0


@pytest.mark.asyncio
async def test_upload_group_file_does_not_upload_when_preflight_fails(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    apk_path = tmp_path / "Clash_of_Clans_99.1.2_APKCombo.apk"
    apk_path.write_bytes(b"apk")
    apk = module.DownloadedApk(filename=apk_path.name, path=apk_path)
    upload_calls = 0

    class FakeBot:
        self_id = "1940196378"

        async def call_api(self, api, **kwargs):
            nonlocal upload_calls
            if api == "get_group_root_files":
                raise TimeoutError("group file listing timed out")
            if api == "upload_group_file":
                upload_calls += 1
            raise AssertionError(f"unexpected API: {api}")

    monkeypatch.setattr(module, "_select_bot", lambda: FakeBot())

    result = await module._upload_group_file(607572668, apk)

    assert result.status is module.UploadStatus.UNKNOWN
    assert "preflight failed" in result.detail
    assert upload_calls == 0


@pytest.mark.asyncio
async def test_check_coc_apk_update_reports_upload_failure(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()

    sent_messages = []

    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_group_id",
        607572668,
        raising=False,
    )

    version = module.CocVersion(
        version_name="18.200.19",
        version_code="180200020",
        update_date="2026-03-20T11:44:56+07:00",
    )

    async def fake_get_latest_version(_client):
        return version

    async def fake_download_apk(_client, _shared_dir):
        target = shared_dir / "Clash_of_Clans_18.200.19_APKPure.apk"
        target.write_bytes(b"apk")
        return module.DownloadedApk(filename=target.name, path=target)

    async def fake_upload_group_file(_group_id, _apk):
        return module.UploadResult(
            status=module.UploadStatus.FAILED,
            detail="ENOENT: no such file or directory, open '/shared/missing.apk'",
        )

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )
    monkeypatch.setattr(module._APK_SOURCE, "download_apk", fake_download_apk)
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)

    await module.check_coc_apk_update()

    assert sent_messages == [
        (
            607572668,
            "[CoC APK] New version detected\n"
            "version_name: 18.200.19\n"
            "version_code: 180200020\n"
            "update_date: 2026-03-20T11:44:56+07:00",
        ),
        (
            607572668,
            "[CoC APK] Upload failed: ENOENT: no such file or directory, open '/shared/missing.apk'",
        ),
    ]


# ---------------------------------------------------------------------------
# error / alert tests
# ---------------------------------------------------------------------------


class FakeBot:
    def __init__(self):
        self.calls = []

    async def call_api(self, api: str, **kwargs):
        self.calls.append((api, kwargs))
        return {"status": "ok"}


class FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid/apk")
            response = httpx.Response(
                self.status_code,
                headers=self.headers,
                request=request,
            )
            raise httpx.HTTPStatusError(
                "download failed", request=request, response=response
            )

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeStreamContext:
    def __init__(self, response: FakeStreamResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeHttpClient:
    def __init__(self, response: FakeStreamResponse):
        self._response = response

    def stream(self, method: str, url: str, headers: dict[str, str]):
        return FakeStreamContext(self._response)


@pytest.mark.asyncio
async def test_check_coc_apk_update_catches_error_and_alerts_superuser(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    fake_bot = FakeBot()
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    module._FAILURE_COUNT_BY_KEY.clear()

    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setenv("SUPERUSERS", '["1669790626"]')
    monkeypatch.setattr(
        module,
        "driver",
        SimpleNamespace(config=SimpleNamespace(superusers=set())),
    )
    monkeypatch.setattr(module, "get_bots", lambda: {"bot": fake_bot})

    async def fake_get_latest_version(_client):
        raise module.httpx.ConnectError("proxy connect failed")

    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )

    for _ in range(4):
        await module.check_coc_apk_update()

    assert fake_bot.calls == []
    assert module._FAILURE_COUNT_BY_KEY["coc-checker-check-failed"] == 4

    await module.check_coc_apk_update()

    assert len(fake_bot.calls) == 1
    api, payload = fake_bot.calls[0]
    assert api == "send_private_msg"
    assert payload["user_id"] == 1669790626
    assert (
        "Scheduled check failed: ConnectError: proxy connect failed"
        in payload["message"]
    )
    assert module._FAILURE_COUNT_BY_KEY["coc-checker-check-failed"] == 0


@pytest.mark.asyncio
async def test_check_coc_apk_update_resets_failure_counter_after_success(
    coc_apk_checker_module, monkeypatch, tmp_path
):
    module = coc_apk_checker_module
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    module._FAILURE_COUNT_BY_KEY.clear()

    monkeypatch.setattr(module, "_should_enable_checker", lambda: True)
    monkeypatch.setattr(module, "_shared_dir", lambda: shared_dir)
    monkeypatch.setattr(
        module.plugin_config,
        "coc_checker_group_id",
        607572668,
        raising=False,
    )

    async def fake_get_latest_version(_client):
        return module.CocVersion(
            version_name="18.200.19",
            version_code="180200020",
            update_date="2026-03-20T11:44:56+07:00",
        )

    async def fake_download_apk(_client, _shared_dir):
        target = shared_dir / "Clash_of_Clans_18.200.19_APKPure.apk"
        target.write_bytes(b"apk")
        return module.DownloadedApk(filename=target.name, path=target)

    async def fake_upload_group_file(_group_id, _apk):
        return module.UploadResult(status=module.UploadStatus.CONFIRMED)

    async def fake_send_group_message(_group_id, _message):
        return None

    module._FAILURE_COUNT_BY_KEY["coc-checker-check-failed"] = 3
    monkeypatch.setattr(
        module._APK_SOURCE, "get_latest_version", fake_get_latest_version
    )
    monkeypatch.setattr(module._APK_SOURCE, "download_apk", fake_download_apk)
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)

    await module.check_coc_apk_update()

    assert "coc-checker-check-failed" not in module._FAILURE_COUNT_BY_KEY


# ---------------------------------------------------------------------------
# ApkPureSource download tests (direct, no NoneBot)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apkpure_download_accepts_octet_stream_with_apk_filename(tmp_path):
    sources = _import_sources()
    source = sources.ApkPureSource()

    response = FakeStreamResponse(
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="Clash of Clans_18.367.1_APKPure.apk"',
        },
        chunks=[b"PK\x03\x04apk-data"],
    )
    client = FakeHttpClient(response)

    downloaded = await source.download_apk(client, tmp_path)

    assert downloaded.filename == "Clash_of_Clans_18.367.1_APKPure.apk"
    assert downloaded.path.read_bytes() == b"PK\x03\x04apk-data"


@pytest.mark.asyncio
async def test_apkpure_download_normalizes_plus_named_filename(tmp_path):
    sources = _import_sources()
    source = sources.ApkPureSource()

    response = FakeStreamResponse(
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="Clash+of+Clans_18.367.1_APKPure.apk"',
        },
        chunks=[b"PK\x03\x04apk-data"],
    )
    client = FakeHttpClient(response)

    downloaded = await source.download_apk(client, tmp_path)

    assert downloaded.filename == "Clash_of_Clans_18.367.1_APKPure.apk"
    assert downloaded.path.name == "Clash_of_Clans_18.367.1_APKPure.apk"


@pytest.mark.asyncio
async def test_apkpure_download_rejects_generic_binary_without_zip_signature(tmp_path):
    sources = _import_sources()
    source = sources.ApkPureSource()

    response = FakeStreamResponse(
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="Clash of Clans_18.367.1_APKPure.apk"',
        },
        chunks=[b"not-an-apk"],
    )
    client = FakeHttpClient(response)

    with pytest.raises(
        RuntimeError, match="Unexpected APK payload for generic binary response"
    ):
        await source.download_apk(client, tmp_path)

    assert list(tmp_path.iterdir()) == []

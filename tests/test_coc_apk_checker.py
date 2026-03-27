import importlib
import sys
from pathlib import Path

import nonebot
import pytest


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


def test_select_latest_version_filters_only_apk(coc_apk_checker_module):
    module = coc_apk_checker_module

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

    latest = module._select_latest_version(payload)

    assert latest == module.CocVersion(
        version_name="18.200.19",
        version_code="180200020",
        update_date="2026-03-20T11:44:56+07:00",
    )


def test_extract_version_name_from_filename(coc_apk_checker_module):
    module = coc_apk_checker_module

    assert (
        module._extract_version_name_from_filename(
            "Clash of Clans_18.200.19_APKPure.apk"
        )
        == "18.200.19"
    )
    assert module._extract_version_name_from_filename("other.apk") is None


def test_decode_content_disposition_filename(coc_apk_checker_module):
    module = coc_apk_checker_module

    header = 'attachment; filename="Clash of Clans_18.200.19_APKPure.apk"'
    assert (
        module._decode_content_disposition_filename(header)
        == "Clash of Clans_18.200.19_APKPure.apk"
    )


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


@pytest.mark.asyncio
async def test_check_coc_apk_update_skips_when_latest_file_exists(
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

    async def fake_fetch_latest_version(_client):
        return module.CocVersion(
            version_name="18.200.19",
            version_code="180200020",
            update_date="2026-03-20T11:44:56+07:00",
        )

    async def fake_download_latest_apk(_client, _shared_dir):
        download_calls.append(True)
        return module.DownloadedApk(
            filename="Clash of Clans_18.200.19_APKPure.apk",
            path=shared_dir / "Clash of Clans_18.200.19_APKPure.apk",
        )

    async def fake_upload_group_file(_group_id, _apk):
        upload_calls.append(True)
        return module.UploadResult(ok=True, detail="")

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(module, "_fetch_latest_version", fake_fetch_latest_version)
    monkeypatch.setattr(module, "_download_latest_apk", fake_download_latest_apk)
    monkeypatch.setattr(module, "_upload_group_file", fake_upload_group_file)
    monkeypatch.setattr(module, "_send_group_message", fake_send_group_message)

    await module.check_coc_apk_update()

    assert sent_messages == []
    assert download_calls == []
    assert upload_calls == []


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
        filename="Clash of Clans_18.200.19_APKPure.apk",
        path=shared_dir / "Clash of Clans_18.200.19_APKPure.apk",
    )

    async def fake_fetch_latest_version(_client):
        return version

    async def fake_download_latest_apk(_client, _shared_dir):
        downloaded.path.write_bytes(b"apk")
        return downloaded

    async def fake_upload_group_file(group_id, apk):
        uploaded.append((group_id, apk))
        return module.UploadResult(ok=True, detail="")

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(module, "_fetch_latest_version", fake_fetch_latest_version)
    monkeypatch.setattr(module, "_download_latest_apk", fake_download_latest_apk)
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

    async def fake_fetch_latest_version(_client):
        return version

    async def fake_download_latest_apk(_client, _shared_dir):
        target = shared_dir / "Clash of Clans_18.200.19_APKPure.apk"
        target.write_bytes(b"apk")
        return module.DownloadedApk(filename=target.name, path=target)

    async def fake_upload_group_file(_group_id, _apk):
        return module.UploadResult(
            ok=False,
            detail="ENOENT: no such file or directory, open '/shared/missing.apk'",
        )

    async def fake_send_group_message(group_id, message):
        sent_messages.append((group_id, message))

    monkeypatch.setattr(module, "_fetch_latest_version", fake_fetch_latest_version)
    monkeypatch.setattr(module, "_download_latest_apk", fake_download_latest_apk)
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

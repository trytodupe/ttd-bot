from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

import httpx
from nonebot import get_bots, get_driver, get_plugin_config, require
from nonebot.adapters.onebot.v11 import Bot
from nonebot.plugin import PluginMetadata

from .config import Config

logger = logging.getLogger(__name__)

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name="coc-apk-checker",
    description="Check Clash of Clans APK updates and upload new APK files from /shared.",
    usage="Runs automatically every 30 minutes inside Docker when /shared is available.",
    config=Config,
)

plugin_config = get_plugin_config(Config)
_HISTORY_URL = (
    "https://tapi.pureapk.com/v3/get_app_his_version"
    "?package_name=com.supercell.clashofclans&hl=en"
)
_DOWNLOAD_URL = "https://d.apkpure.com/b/APK/com.supercell.clashofclans?version=latest"
_HISTORY_HEADERS = {
    "Ual-Access-Businessid": "projecta",
    "Ual-Access-ProjectA": '{"device_info":{"os_ver":"35"}}',
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://apkpure.com",
    "Referer": "https://apkpure.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
}
_DOWNLOAD_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://apkpure.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
    ),
}
_APK_MIME = "application/vnd.android.package-archive"
_FILENAME_RE = re.compile(r'^Clash of Clans_(?P<version_name>[^/]+?)_APKPure\.apk$')
_JOB_ID = "coc_apk_checker_poll"
_CHECK_LOCK = asyncio.Lock()
_ALERT_KEYS_SENT: set[str] = set()

driver = get_driver()


@dataclass(frozen=True)
class CocVersion:
    version_name: str
    version_code: str
    update_date: str

    @property
    def version_code_int(self) -> int:
        try:
            return int(self.version_code)
        except (TypeError, ValueError):
            return -1


@dataclass(frozen=True)
class DownloadedApk:
    filename: str
    path: Path


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    detail: str


def _parse_superusers(value: str) -> list[str]:
    raw = value.strip()
    if not raw:
        return []

    parsed: Any = None
    try:
        parsed = json.loads(raw)
    except Exception:
        try:
            parsed = ast.literal_eval(raw)
        except Exception:
            parsed = None

    if isinstance(parsed, (list, tuple, set)):
        return [str(item).strip().strip('"\'') for item in parsed if str(item).strip()]

    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip().strip('"\'')]

    return [item.strip().strip('"\'') for item in raw.replace(",", " ").split() if item.strip()]


def _resolve_primary_superuser() -> int | None:
    env_value = os.getenv("SUPERUSERS", "")
    candidates = _parse_superusers(env_value)

    if not candidates:
        fallback_superusers = getattr(driver.config, "superusers", set())
        if fallback_superusers:
            normalized = [str(item).strip() for item in fallback_superusers if str(item).strip()]
            candidates = sorted(
                normalized,
                key=lambda item: (not item.isdigit(), int(item) if item.isdigit() else item),
            )

    for candidate in candidates:
        if candidate.isdigit():
            return int(candidate)

    logger.warning("No valid superuser found for CoC checker alert")
    return None


async def _send_private_alert(message: str) -> bool:
    target_user_id = _resolve_primary_superuser()
    if target_user_id is None:
        return False

    bot = _select_bot()
    if bot is None:
        logger.warning("No available bot to send CoC checker alert")
        return False

    try:
        await bot.call_api("send_private_msg", user_id=target_user_id, message=message)
        logger.info("Sent CoC checker alert to superuser %s", target_user_id)
        return True
    except Exception as exc:
        logger.warning("Failed to send CoC checker alert: %s", exc)
        return False


async def _send_private_alert_once(key: str, message: str) -> bool:
    if key in _ALERT_KEYS_SENT:
        return False

    _ALERT_KEYS_SENT.add(key)
    return await _send_private_alert(message)


def _clear_alert(key: str) -> None:
    _ALERT_KEYS_SENT.discard(key)


def _is_running_in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("KUBERNETES_SERVICE_HOST") is not None


def _shared_dir() -> Path:
    return Path(plugin_config.coc_checker_shared_dir)


def _should_enable_checker() -> bool:
    shared_dir = _shared_dir()
    return _is_running_in_docker() and shared_dir.is_dir()


def _candidate_apk_files(shared_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in shared_dir.iterdir()
            if path.is_file() and _extract_version_name_from_filename(path.name)
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _extract_version_name_from_filename(filename: str) -> str | None:
    match = _FILENAME_RE.fullmatch(filename)
    if not match:
        return None
    return match.group("version_name")


def _latest_local_version_name(shared_dir: Path) -> str | None:
    for path in _candidate_apk_files(shared_dir):
        version_name = _extract_version_name_from_filename(path.name)
        if version_name:
            return version_name
    return None


def _has_local_version_name(shared_dir: Path, version_name: str) -> bool:
    for path in _candidate_apk_files(shared_dir):
        if _extract_version_name_from_filename(path.name) == version_name:
            return True
    return False


def _parse_version_row(item: Any) -> CocVersion | None:
    if not isinstance(item, dict):
        return None

    asset = item.get("asset")
    if not isinstance(asset, dict) or asset.get("type") != "APK":
        return None

    version_name = str(item.get("version_name", "")).strip()
    version_code = str(item.get("version_code", "")).strip()
    update_date = str(item.get("update_date", "")).strip()
    if not version_name or not version_code or not update_date:
        return None

    return CocVersion(
        version_name=version_name,
        version_code=version_code,
        update_date=update_date,
    )


def _select_latest_version(payload: dict[str, Any]) -> CocVersion | None:
    version_list = payload.get("version_list")
    if not isinstance(version_list, list):
        return None

    versions = [version for item in version_list if (version := _parse_version_row(item)) is not None]
    if not versions:
        return None

    return max(versions, key=lambda version: (version.version_code_int, version.update_date))


def _decode_content_disposition_filename(header_value: str | None) -> str | None:
    if not header_value:
        return None

    for part in header_value.split(";"):
        key, separator, raw_value = part.strip().partition("=")
        if not separator:
            continue

        normalized_key = key.lower()
        value = raw_value.strip().strip('"')
        if normalized_key == "filename*":
            try:
                _, _, encoded_name = value.split("'", 2)
            except ValueError:
                return value
            return unquote(encoded_name)
        if normalized_key == "filename":
            return value
    return None


def _is_valid_apk_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("Content-Type", "")
    return response.status_code == 200 and _APK_MIME in content_type


def _build_http_client() -> httpx.AsyncClient:
    timeout = max(10, int(plugin_config.coc_checker_timeout_seconds))
    proxy = str(plugin_config.coc_checker_proxy).strip() or None
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=30.0),
        proxy=proxy,
        trust_env=True,
    )


async def _fetch_latest_version(client: httpx.AsyncClient) -> CocVersion | None:
    response = await client.get(_HISTORY_URL, headers=_HISTORY_HEADERS)
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    return _select_latest_version(payload)


async def _download_latest_apk(client: httpx.AsyncClient, shared_dir: Path) -> DownloadedApk:
    async with client.stream("GET", _DOWNLOAD_URL, headers=_DOWNLOAD_HEADERS) as response:
        response.raise_for_status()
        if not _is_valid_apk_response(response):
            raise RuntimeError(
                "Unexpected download response: "
                f"status={response.status_code}, content-type={response.headers.get('Content-Type', '')}"
            )

        filename = _decode_content_disposition_filename(
            response.headers.get("Content-Disposition")
        )
        if not filename:
            raise RuntimeError("Missing Content-Disposition filename in download response")

        target_path = shared_dir / filename
        temp_path = shared_dir / f".{filename}.part"
        temp_path.unlink(missing_ok=True)

        with temp_path.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                if chunk:
                    handle.write(chunk)

        temp_path.replace(target_path)
        return DownloadedApk(filename=filename, path=target_path)


def _select_bot() -> Bot | None:
    bots = get_bots()
    if not bots:
        return None
    return cast(Bot, next(iter(bots.values())))


async def _send_group_message(group_id: int, message: str) -> None:
    bot = _select_bot()
    if bot is None:
        logger.warning("No available bot to send CoC update message")
        return
    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=message)
    except Exception as exc:
        logger.warning("Failed to send CoC group message: %s", exc)


def _format_version_message(version: CocVersion) -> str:
    return (
        "[CoC APK] New version detected\n"
        f"version_name: {version.version_name}\n"
        f"version_code: {version.version_code}\n"
        f"update_date: {version.update_date}"
    )


def _extract_upload_error(result: Any) -> str:
    if isinstance(result, dict):
        status = str(result.get("status", "")).strip()
        retcode = result.get("retcode")
        if status == "ok" and retcode == 0:
            return ""
        for key in ("wording", "message"):
            value = str(result.get(key, "")).strip()
            if value:
                return value
        return json.dumps(result, ensure_ascii=False)
    return str(result)


async def _upload_group_file(group_id: int, apk: DownloadedApk) -> UploadResult:
    bot = _select_bot()
    if bot is None:
        return UploadResult(ok=False, detail="No available bot")

    try:
        result = await bot.call_api(
            "upload_group_file",
            group_id=group_id,
            file=apk.path.resolve().as_uri(),
            name=apk.filename,
        )
    except Exception as exc:
        return UploadResult(ok=False, detail=f"{type(exc).__name__}: {exc}")

    error_detail = _extract_upload_error(result)
    if error_detail:
        return UploadResult(ok=False, detail=error_detail)
    return UploadResult(ok=True, detail="")


async def _announce_upload_failure(group_id: int, detail: str) -> None:
    await _send_group_message(group_id, f"[CoC APK] Upload failed: {detail}")


async def check_coc_apk_update() -> None:
    if not _should_enable_checker():
        return

    async with _CHECK_LOCK:
        shared_dir = _shared_dir()
        shared_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with _build_http_client() as client:
                latest_version = await _fetch_latest_version(client)
                if latest_version is None:
                    logger.warning("CoC checker did not find any APK versions")
                    return

                local_version_name = _latest_local_version_name(shared_dir)
                if _has_local_version_name(shared_dir, latest_version.version_name):
                    logger.debug("CoC APK already up to date: %s", local_version_name)
                    _clear_alert("coc-checker-check-failed")
                    return

                logger.info(
                    "Detected new CoC APK version: %s (local=%s)",
                    latest_version.version_name,
                    local_version_name or "none",
                )
                await _send_group_message(
                    int(plugin_config.coc_checker_group_id),
                    _format_version_message(latest_version),
                )

                try:
                    downloaded_apk = await _download_latest_apk(client, shared_dir)
                except Exception as exc:
                    logger.warning("Failed to download CoC APK: %s", exc)
                    await _announce_upload_failure(
                        int(plugin_config.coc_checker_group_id),
                        f"download error: {type(exc).__name__}: {exc}",
                    )
                    return

            upload_result = await _upload_group_file(
                int(plugin_config.coc_checker_group_id),
                downloaded_apk,
            )
            if upload_result.ok:
                _clear_alert("coc-checker-check-failed")
                logger.info("Uploaded CoC APK successfully: %s", downloaded_apk.filename)
                return

            logger.warning("Failed to upload CoC APK: %s", upload_result.detail)
            await _announce_upload_failure(
                int(plugin_config.coc_checker_group_id),
                upload_result.detail,
            )
        except Exception as exc:
            logger.exception("CoC APK check failed: %s", exc)
            await _send_private_alert_once(
                "coc-checker-check-failed",
                f"[coc-apk-checker] Scheduled check failed: {type(exc).__name__}: {exc}",
            )


@driver.on_startup
async def _start_coc_checker() -> None:
    if not _should_enable_checker():
        logger.info("CoC APK checker disabled because Docker or /shared is unavailable")
        return

    scheduler.add_job(
        check_coc_apk_update,
        "interval",
        seconds=max(60, int(plugin_config.coc_checker_interval_seconds)),
        id=_JOB_ID,
        next_run_time=datetime.now() + timedelta(seconds=5),
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=300,
    )
    logger.info("CoC APK checker scheduled")


@driver.on_shutdown
async def _stop_coc_checker() -> None:
    job = scheduler.get_job(_JOB_ID)
    if job:
        job.remove()

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote_plus

import httpx
from nonebot import (
    get_bots,
    get_driver,
    get_plugin_config,
    logger as nonebot_logger,
    on,
    require,
)
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.plugin import PluginMetadata

from .config import Config
from .sources import (
    CocVersion,
    DownloadedApk,
    UploadResult,
    UploadStatus,
    create_source,
)

logger = nonebot_logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name="coc-apk-checker",
    description="Check Clash of Clans APK updates and upload new APK files from /shared.",
    usage="Runs automatically every 30 minutes when /shared is available.",
    config=Config,
)

plugin_config = get_plugin_config(Config)
_APK_SOURCE = create_source(plugin_config.coc_checker_source)

_FILENAME_RE = re.compile(r'^Clash_of_Clans_(?P<version_name>[^/]+?)_[^/]+\.apk$')
_JOB_ID = "coc_apk_checker_poll"
_CHECK_LOCK = asyncio.Lock()
_FAILURE_COUNT_BY_KEY: dict[str, int] = {}
_ALERT_FAILURE_THRESHOLD = 5
_uploaded_versions: set[str] = set()
_UPLOADED_MARKER = ".uploaded_versions.json"

driver = get_driver()


@dataclass
class PendingUpload:
    group_id: int
    filename: str
    file_size: int
    receipt: asyncio.Future[str]


_pending_upload: PendingUpload | None = None


# ---------------------------------------------------------------------------
# alert / notification helpers
# ---------------------------------------------------------------------------


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
        logger.info("Sent CoC checker alert to superuser {}", target_user_id)
        return True
    except Exception as exc:
        logger.warning("Failed to send CoC checker alert: {}", exc)
        return False


async def _maybe_alert_after_failure(key: str, message: str) -> bool:
    failure_count = _FAILURE_COUNT_BY_KEY.get(key, 0) + 1
    _FAILURE_COUNT_BY_KEY[key] = failure_count
    if failure_count < _ALERT_FAILURE_THRESHOLD:
        logger.warning(
            "CoC checker failure {}/{} for {}",
            failure_count,
            _ALERT_FAILURE_THRESHOLD,
            key,
        )
        return False

    _FAILURE_COUNT_BY_KEY[key] = 0
    return await _send_private_alert(message)


def _reset_failure_count(key: str) -> None:
    _FAILURE_COUNT_BY_KEY.pop(key, None)


# ---------------------------------------------------------------------------
# filesystem / filename helpers
# ---------------------------------------------------------------------------


def _shared_dir() -> Path:
    return Path(plugin_config.coc_checker_shared_dir)


def _load_uploaded_versions(shared_dir: Path) -> set[str]:
    marker = shared_dir / _UPLOADED_MARKER
    if marker.is_file():
        try:
            return set(json.loads(marker.read_text()))
        except (json.JSONDecodeError, TypeError):
            pass
    return set()


def _save_uploaded_versions(shared_dir: Path) -> None:
    marker = shared_dir / _UPLOADED_MARKER
    marker.write_text(json.dumps(sorted(_uploaded_versions)))


def _should_enable_checker() -> bool:
    shared_dir = _shared_dir()
    return shared_dir.is_dir()


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


def _normalize_apk_filename(filename: str) -> str:
    normalized_filename = unquote_plus(filename.strip())
    return re.sub(r"^Clash[ _]+of[ _]+Clans_", "Clash_of_Clans_", normalized_filename)


def _extract_version_name_from_filename(filename: str) -> str | None:
    normalized_filename = _normalize_apk_filename(filename)
    match = _FILENAME_RE.fullmatch(normalized_filename)
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
    return _local_apk_for_version(shared_dir, version_name) is not None


def _local_apk_for_version(shared_dir: Path, version_name: str) -> Path | None:
    for path in _candidate_apk_files(shared_dir):
        if _extract_version_name_from_filename(path.name) == version_name:
            return path
    return None
    return False


# ---------------------------------------------------------------------------
# bot / messaging helpers
# ---------------------------------------------------------------------------


def _build_http_client() -> httpx.AsyncClient:
    timeout = max(10, int(plugin_config.coc_checker_timeout_seconds))
    proxy = str(plugin_config.coc_checker_proxy).strip() or None
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=30.0),
        proxy=proxy,
        trust_env=True,
    )


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
        logger.warning("Failed to send CoC group message: {}", exc)


def _format_version_message(version: CocVersion) -> str:
    return (
        "[CoC APK] New version detected\n"
        f"version_name: {version.version_name}\n"
        f"version_code: {version.version_code}\n"
        f"update_date: {version.update_date}"
    )


def _extract_upload_error(result: Any) -> str:
    if isinstance(result, dict):
        if str(result.get("file_id", "")).strip():
            return ""

        nested_data = result.get("data")
        if isinstance(nested_data, dict) and str(nested_data.get("file_id", "")).strip():
            return ""

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


def _extract_upload_file_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    file_id = str(result.get("file_id", "")).strip()
    if file_id:
        return file_id
    nested_data = result.get("data")
    if isinstance(nested_data, dict):
        file_id = str(nested_data.get("file_id", "")).strip()
        if file_id:
            return file_id
    return None


def _matches_uploaded_filename(expected: str, actual: str) -> bool:
    return actual == expected or re.fullmatch(
        rf"{re.escape(expected)}\.\d+", actual
    ) is not None


def _matching_file_id(
    file_data: Any,
    *,
    filename: str,
    file_size: int,
) -> str | None:
    if not isinstance(file_data, dict):
        return None
    actual_name = str(
        file_data.get("file_name")
        or file_data.get("name")
        or file_data.get("file")
        or ""
    ).strip()
    if not _matches_uploaded_filename(filename, actual_name):
        return None
    try:
        actual_size = int(file_data.get("file_size") or file_data.get("size"))
    except (TypeError, ValueError):
        return None
    if actual_size != file_size:
        return None
    file_id = str(file_data.get("file_id") or file_data.get("id") or "").strip()
    return file_id or None


async def _find_uploaded_group_file(
    bot: Bot,
    group_id: int,
    apk: DownloadedApk,
) -> str | None:
    result = await bot.call_api(
        "get_group_root_files",
        _timeout=30,
        group_id=group_id,
    )
    if not isinstance(result, dict) or not isinstance(result.get("files"), list):
        raise RuntimeError("get_group_root_files returned an invalid response")

    file_size = apk.path.stat().st_size
    for file_data in result["files"]:
        if not isinstance(file_data, dict):
            continue
        if str(file_data.get("uploader", "")).strip() != str(bot.self_id):
            continue
        if file_id := _matching_file_id(
            file_data,
            filename=apk.filename,
            file_size=file_size,
        ):
            return file_id
    return None


def _handle_message_sent_event(event: Event) -> None:
    pending = _pending_upload
    if pending is None or pending.receipt.done():
        return
    if (
        event.post_type != "message_sent"
        or getattr(event, "message_type", None) != "group"
        or getattr(event, "group_id", None) != pending.group_id
        or str(getattr(event, "user_id", "")) != str(event.self_id)
    ):
        return

    message = getattr(event, "message", None)
    if not isinstance(message, list):
        return
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "file":
            continue
        if file_id := _matching_file_id(
            segment.get("data"),
            filename=pending.filename,
            file_size=pending.file_size,
        ):
            pending.receipt.set_result(file_id)
            return


def _message_sent_result(pending: PendingUpload) -> UploadResult | None:
    if not pending.receipt.done() or pending.receipt.cancelled():
        return None
    return UploadResult(
        status=UploadStatus.CONFIRMED,
        detail="confirmed by message_sent",
        file_id=pending.receipt.result(),
    )


message_sent_matcher = on("message_sent", priority=1, block=False)


@message_sent_matcher.handle()
async def _receive_message_sent(event: Event) -> None:
    _handle_message_sent_event(event)


async def _upload_group_file(group_id: int, apk: DownloadedApk) -> UploadResult:
    global _pending_upload

    bot = _select_bot()
    if bot is None:
        return UploadResult(status=UploadStatus.FAILED, detail="No available bot")

    try:
        existing_file_id = await _find_uploaded_group_file(bot, group_id, apk)
    except Exception as exc:
        return UploadResult(
            status=UploadStatus.UNKNOWN,
            detail=f"group file preflight failed: {type(exc).__name__}: {exc}",
        )
    if existing_file_id:
        return UploadResult(
            status=UploadStatus.CONFIRMED,
            detail="confirmed by group file listing",
            file_id=existing_file_id,
        )

    pending = PendingUpload(
        group_id=group_id,
        filename=apk.filename,
        file_size=apk.path.stat().st_size,
        receipt=asyncio.get_running_loop().create_future(),
    )
    _pending_upload = pending
    upload_task = asyncio.create_task(
        bot.call_api(
            "upload_group_file",
            _timeout=600,
            group_id=group_id,
            file=str(apk.path),
            name=apk.filename,
        )
    )

    try:
        await asyncio.wait(
            {upload_task, pending.receipt},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if message_sent_result := _message_sent_result(pending):
            return message_sent_result

        try:
            result = upload_task.result()
        except Exception as exc:
            try:
                existing_file_id = await _find_uploaded_group_file(bot, group_id, apk)
            except Exception as reconcile_exc:
                if message_sent_result := _message_sent_result(pending):
                    return message_sent_result
                return UploadResult(
                    status=UploadStatus.UNKNOWN,
                    detail=(
                        f"{type(exc).__name__}: {exc}; reconciliation failed: "
                        f"{type(reconcile_exc).__name__}: {reconcile_exc}"
                    ),
                )
            if message_sent_result := _message_sent_result(pending):
                return message_sent_result
            if existing_file_id:
                return UploadResult(
                    status=UploadStatus.CONFIRMED,
                    detail="confirmed by group file listing",
                    file_id=existing_file_id,
                )
            if isinstance(exc, ActionFailed):
                return UploadResult(
                    status=UploadStatus.FAILED,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            return UploadResult(
                status=UploadStatus.UNKNOWN,
                detail=f"{type(exc).__name__}: {exc}",
            )

        if file_id := _extract_upload_file_id(result):
            return UploadResult(
                status=UploadStatus.CONFIRMED,
                detail="confirmed by upload API",
                file_id=file_id,
            )

        try:
            existing_file_id = await _find_uploaded_group_file(bot, group_id, apk)
        except Exception as exc:
            if message_sent_result := _message_sent_result(pending):
                return message_sent_result
            return UploadResult(
                status=UploadStatus.UNKNOWN,
                detail=f"upload response missing file_id; reconciliation failed: {exc}",
            )
        if message_sent_result := _message_sent_result(pending):
            return message_sent_result
        if existing_file_id:
            return UploadResult(
                status=UploadStatus.CONFIRMED,
                detail="confirmed by group file listing",
                file_id=existing_file_id,
            )
        return UploadResult(
            status=UploadStatus.UNKNOWN,
            detail="upload response missing file_id and group file was not found",
        )
    finally:
        if _pending_upload is pending:
            _pending_upload = None
        if not pending.receipt.done():
            pending.receipt.cancel()
        if not upload_task.done():
            upload_task.cancel()
            await asyncio.gather(upload_task, return_exceptions=True)


async def _announce_upload_failure(group_id: int, detail: str) -> None:
    await _send_group_message(group_id, f"[CoC APK] Upload failed: {detail}")


# ---------------------------------------------------------------------------
# main check logic
# ---------------------------------------------------------------------------


async def check_coc_apk_update() -> None:
    if not _should_enable_checker():
        return

    async with _CHECK_LOCK:
        shared_dir = _shared_dir()
        shared_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with _build_http_client() as client:
                latest_version = await _APK_SOURCE.get_latest_version(client)
                if latest_version is None:
                    logger.warning("CoC checker did not find any APK versions")
                    return

                local_version_name = _latest_local_version_name(shared_dir)
                if _has_local_version_name(shared_dir, latest_version.version_name):
                    if latest_version.version_name in _uploaded_versions:
                        logger.info("CoC APK already up to date: {}", local_version_name)
                        _reset_failure_count("coc-checker-check-failed")
                        return

                    # Version exists locally but upload not confirmed — retry
                    local_path = _local_apk_for_version(shared_dir, latest_version.version_name)
                    if local_path is None:
                        return
                    downloaded_apk = DownloadedApk(
                        filename=local_path.name,
                        path=local_path,
                    )
                    logger.info(
                        "Retrying upload for CoC APK {}",
                        latest_version.version_name,
                    )
                else:
                    logger.info(
                        "Detected new CoC APK version: {} (local={})",
                        latest_version.version_name,
                        local_version_name or "none",
                    )
                    await _send_group_message(
                        int(plugin_config.coc_checker_group_id),
                        _format_version_message(latest_version),
                    )

                    try:
                        downloaded_apk = await _APK_SOURCE.download_apk(client, shared_dir)
                    except Exception as exc:
                        logger.warning("Failed to download CoC APK: {}", exc)
                        await _announce_upload_failure(
                            int(plugin_config.coc_checker_group_id),
                            f"download error: {type(exc).__name__}: {exc}",
                        )
                        return

            upload_result = await _upload_group_file(
                int(plugin_config.coc_checker_group_id),
                downloaded_apk,
            )
            if upload_result.status is UploadStatus.CONFIRMED:
                _uploaded_versions.add(latest_version.version_name)
                _save_uploaded_versions(shared_dir)
                _reset_failure_count("coc-checker-check-failed")
                logger.info(
                    "Confirmed CoC APK upload: {} (file_id={}, {})",
                    downloaded_apk.filename,
                    upload_result.file_id or "unknown",
                    upload_result.detail,
                )
                return

            if upload_result.status is UploadStatus.FAILED:
                logger.warning("Failed to upload CoC APK: {}", upload_result.detail)
                await _announce_upload_failure(
                    int(plugin_config.coc_checker_group_id),
                    upload_result.detail,
                )
            else:
                logger.warning(
                    "CoC APK upload outcome is unknown; will reconcile before retry: {}",
                    upload_result.detail,
                )
        except Exception as exc:
            logger.opt(exception=True).error("CoC APK check failed: {}", exc)
            await _maybe_alert_after_failure(
                "coc-checker-check-failed",
                f"[coc-apk-checker] Scheduled check failed: {type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


@driver.on_startup
async def _start_coc_checker() -> None:
    if not _should_enable_checker():
        nonebot_logger.info("CoC APK checker disabled because /shared is unavailable")
        return

    _uploaded_versions.update(_load_uploaded_versions(_shared_dir()))

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
    nonebot_logger.info("CoC APK checker scheduled")


@driver.on_shutdown
async def _stop_coc_checker() -> None:
    job = scheduler.get_job(_JOB_ID)
    if job:
        job.remove()

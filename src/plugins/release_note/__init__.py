import ast
import json
import logging
import os
from typing import Any, Optional

from nonebot import get_bots, get_driver, on_command, require
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

logger = logging.getLogger(__name__)

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import plugin_config

__plugin_meta__ = PluginMetadata(
    name="release-note",
    description="自动发布版本更新日志",
    usage="Bot启动时自动检查版本更新并发布",
)

GITHUB_API_BASE = f"https://api.github.com/repos/{plugin_config.github_repo_owner}/{plugin_config.github_repo_name}"
LAST_DEPLOYED_TAG = plugin_config.last_deployed_tag
MAX_LONGNICK_LENGTH = 50

GITHUB_AUTH_FAILURE_HINTS = (
    "bad credentials",
    "expired",
    "requires authentication",
    "resource not accessible by personal access token",
)

_ALERT_KEYS_SENT: set[str] = set()

driver = get_driver()


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


def _resolve_primary_superuser() -> Optional[int]:
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

    logger.warning("No valid superuser found for release-note alert")
    return None


async def _send_private_alert(message: str) -> bool:
    target_user_id = _resolve_primary_superuser()
    if target_user_id is None:
        return False

    bots = get_bots()
    if not bots:
        logger.warning("No available bot to send release-note alert")
        return False

    bot = next(iter(bots.values()))
    try:
        await bot.call_api("send_private_msg", user_id=target_user_id, message=message)
        logger.info("Sent release-note alert to superuser %s", target_user_id)
        return True
    except Exception as exc:
        logger.warning("Failed to send release-note alert: %s", exc)
        return False


async def _send_private_alert_once(key: str, message: str) -> bool:
    if key in _ALERT_KEYS_SENT:
        return False

    _ALERT_KEYS_SENT.add(key)
    return await _send_private_alert(message)


def _select_bot():
    bots = get_bots()
    if not bots:
        return None
    return next(iter(bots.values()))


def _is_github_auth_failure(status_code: int, body_text: str) -> bool:
    if status_code not in (401, 403):
        return False

    normalized_text = (body_text or "").lower()
    return any(keyword in normalized_text for keyword in GITHUB_AUTH_FAILURE_HINTS)


def _normalize_longnick_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _fit_longnick_text(text: str, max_length: int = MAX_LONGNICK_LENGTH) -> str:
    normalized = _normalize_longnick_text(text)
    if len(normalized) <= max_length:
        return normalized

    if max_length <= 0:
        return ""

    return normalized[:max_length].rstrip(" .,;:，。；：-_+/")


async def _notify_github_auth_failure(operation: str, status_code: int, body_text: str) -> bool:
    if not _is_github_auth_failure(status_code, body_text):
        return False

    message = (
        f"[release-note] GitHub token/auth failure ({status_code}) during {operation}. "
        "Check GITHUB_TOKEN and repo tag permissions."
    )
    return await _send_private_alert_once("github-auth-invalid", message)


async def get_github_token() -> Optional[str]:
    token = plugin_config.github_token
    if not token:
        logger.warning("GITHUB_TOKEN not found in configuration")
    return token


async def get_current_version() -> Optional[str]:
    version = os.getenv("VERSION")
    if not version:
        logger.warning("VERSION not found in environment variables")
    return version


async def get_tag_commit_sha(tag_name: str) -> Optional[str]:
    tag_ref = await _get_tag_ref(tag_name)
    if tag_ref is None:
        return None

    return await _resolve_tag_commit_sha(tag_name, tag_ref)


async def _get_tag_ref(tag_name: str) -> Optional[dict]:
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/vnd.github.v3+json"}
            token = await get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            response = await client.get(
                f"{GITHUB_API_BASE}/git/refs/tags/{tag_name}",
                headers=headers,
                timeout=30.0,
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 404:
                logger.info("Tag %s not found", tag_name)
                return None

            await _notify_github_auth_failure(
                "_get_tag_ref",
                response.status_code,
                response.text,
            )
            logger.error("Failed to get tag %s: %s", tag_name, response.status_code)
            return None
    except Exception as exc:
        logger.error("Error getting tag ref: %s", exc)
        return None


async def _resolve_tag_commit_sha(tag_name: str, tag_ref: dict) -> Optional[str]:
    import httpx

    try:
        if tag_ref["object"]["type"] != "tag":
            return tag_ref["object"]["sha"]

        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/vnd.github.v3+json"}
            token = await get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            tag_response = await client.get(
                tag_ref["object"]["url"],
                headers=headers,
                timeout=30.0,
            )
            if tag_response.status_code == 200:
                return tag_response.json()["object"]["sha"]

            await _notify_github_auth_failure(
                "get_tag_commit_sha:resolve_annotated_tag",
                tag_response.status_code,
                tag_response.text,
            )
            logger.error(
                "Failed to resolve annotated tag %s: %s",
                tag_name,
                tag_response.status_code,
            )
            return None
    except Exception as exc:
        logger.error("Error resolving tag commit SHA: %s", exc)
        return None


async def get_tag_message(tag_name: str) -> Optional[str]:
    tag_ref = await _get_tag_ref(tag_name)
    if tag_ref is None:
        return None

    import httpx

    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = await get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"

        async with httpx.AsyncClient() as client:
            if tag_ref["object"]["type"] == "tag":
                tag_response = await client.get(
                    tag_ref["object"]["url"],
                    headers=headers,
                    timeout=30.0,
                )
                if tag_response.status_code != 200:
                    await _notify_github_auth_failure(
                        "get_tag_message:resolve_annotated_tag",
                        tag_response.status_code,
                        tag_response.text,
                    )
                    logger.error(
                        "Failed to resolve annotated tag message %s: %s",
                        tag_name,
                        tag_response.status_code,
                    )
                    return None

                message = str(tag_response.json().get("message", "")).splitlines()[0].strip()
                return message or tag_response.json().get("tag") or None

            commit_response = await client.get(
                tag_ref["object"]["url"],
                headers=headers,
                timeout=30.0,
            )
            if commit_response.status_code != 200:
                await _notify_github_auth_failure(
                    "get_tag_message:resolve_lightweight_tag",
                    commit_response.status_code,
                    commit_response.text,
                )
                logger.error(
                    "Failed to resolve lightweight tag message %s: %s",
                    tag_name,
                    commit_response.status_code,
                )
                return None

            return str(commit_response.json()["commit"]["message"]).splitlines()[0].strip()
    except Exception as exc:
        logger.error("Error getting tag message: %s", exc)
        return None


async def get_commits_between(base_sha: Optional[str], head_sha: str) -> list[dict]:
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/vnd.github.v3+json"}
            token = await get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            if base_sha:
                response = await client.get(
                    f"{GITHUB_API_BASE}/compare/{base_sha}...{head_sha}",
                    headers=headers,
                    timeout=30.0,
                )
            else:
                response = await client.get(
                    f"{GITHUB_API_BASE}/commits",
                    params={"sha": head_sha, "per_page": 10},
                    headers=headers,
                    timeout=30.0,
                )

            if response.status_code == 200:
                data = response.json()
                if base_sha and "commits" in data:
                    return data["commits"]
                if not base_sha:
                    return data
                return []

            await _notify_github_auth_failure(
                "get_commits_between",
                response.status_code,
                response.text,
            )
            logger.error("Failed to get commits: %s", response.status_code)
            return []
    except Exception as exc:
        logger.error("Error getting commits: %s", exc)
        return []


async def get_commit_count_between(base_sha: Optional[str], head_sha: str) -> int:
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/vnd.github.v3+json"}
            token = await get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            if base_sha:
                response = await client.get(
                    f"{GITHUB_API_BASE}/compare/{base_sha}...{head_sha}",
                    headers=headers,
                    timeout=30.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    ahead_by = data.get("ahead_by")
                    if isinstance(ahead_by, int):
                        return ahead_by
                    commits = data.get("commits", [])
                    return len(commits) if isinstance(commits, list) else 0
            else:
                response = await client.get(
                    f"{GITHUB_API_BASE}/commits",
                    params={"sha": head_sha, "per_page": 100},
                    headers=headers,
                    timeout=30.0,
                )
                if response.status_code == 200:
                    commits = response.json()
                    return len(commits) if isinstance(commits, list) else 0

            await _notify_github_auth_failure(
                "get_commit_count_between",
                response.status_code,
                response.text,
            )
            logger.error("Failed to get commit count: %s", response.status_code)
            return 0
    except Exception as exc:
        logger.error("Error getting commit count: %s", exc)
        return 0


async def get_version_tags_at_commit(commit_sha: str) -> list[str]:
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            headers = {"Accept": "application/vnd.github.v3+json"}
            token = await get_github_token()
            if token:
                headers["Authorization"] = f"token {token}"

            response = await client.get(
                f"{GITHUB_API_BASE}/git/refs/tags",
                headers=headers,
                timeout=30.0,
            )

            if response.status_code != 200:
                await _notify_github_auth_failure(
                    "get_version_tags_at_commit",
                    response.status_code,
                    response.text,
                )
                logger.error("Failed to get tags: %s", response.status_code)
                return []

            all_tags = response.json()
            version_tags = []

            for tag_ref in all_tags:
                tag_name = tag_ref["ref"].replace("refs/tags/", "")
                tag_sha = tag_ref["object"]["sha"]
                if tag_name == LAST_DEPLOYED_TAG:
                    continue
                if tag_sha == commit_sha:
                    version_tags.append(tag_name)

            return version_tags
    except Exception as exc:
        logger.error("Error getting version tags at commit: %s", exc)
        return []


async def update_tag(tag_name: str, commit_sha: str, token: str) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}",
            }

            delete_response = await client.delete(
                f"{GITHUB_API_BASE}/git/refs/tags/{tag_name}",
                headers=headers,
                timeout=30.0,
            )

            if delete_response.status_code not in (204, 404):
                await _notify_github_auth_failure(
                    "update_tag:delete",
                    delete_response.status_code,
                    delete_response.text,
                )
                logger.error("Failed to delete old tag: %s", delete_response.status_code)
                return False

            create_response = await client.post(
                f"{GITHUB_API_BASE}/git/refs",
                headers=headers,
                json={"ref": f"refs/tags/{tag_name}", "sha": commit_sha},
                timeout=30.0,
            )

            if create_response.status_code == 201:
                logger.info("Successfully updated tag %s to %s", tag_name, commit_sha)
                return True

            await _notify_github_auth_failure(
                "update_tag:create",
                create_response.status_code,
                create_response.text,
            )
            logger.error(
                "Failed to create tag: %s, %s",
                create_response.status_code,
                create_response.text,
            )
            return False
    except Exception as exc:
        logger.error("Error updating tag: %s", exc)
        return False


def _is_call_api_success(result: Any) -> bool:
    if result is None:
        return True
    if not isinstance(result, dict):
        return True

    status = str(result.get("status", "")).strip().lower()
    retcode = result.get("retcode")
    if status == "failed":
        return False
    if retcode is not None and retcode != 0:
        return False
    return True


async def publish_release_note(release_note: str) -> bool:
    bot = _select_bot()
    if bot is None:
        logger.error("No available bot to publish release note")
        return False

    try:
        result = await bot.call_api("set_self_longnick", longNick=release_note)
        if _is_call_api_success(result):
            logger.info("Successfully published release note")
            return True

        logger.error("Failed to publish release note via call_api: %s", result)
        return False
    except Exception as exc:
        logger.error("Error publishing release note: %s", exc)
        return False


def format_release_note(version: str, tag_message: str, commit_count: int) -> str:
    prefix = _normalize_longnick_text(version)
    summary = _normalize_longnick_text(tag_message) or "deploy"
    suffix = f" (+{max(commit_count, 0)})"

    if not prefix:
        prefix = "deploy"

    base = f"{prefix}: "
    max_summary_length = MAX_LONGNICK_LENGTH - len(base) - len(suffix)
    if max_summary_length < 1:
        return _fit_longnick_text(f"{prefix}{suffix}")

    if len(summary) > max_summary_length:
        summary = summary[:max_summary_length].rstrip(" .,;:，。；：-_+/")
        if not summary:
            summary = "deploy"
            summary = summary[:max_summary_length]

    release_note = f"{base}{summary}{suffix}"
    return _fit_longnick_text(release_note)


async def check_and_publish_release_note() -> None:
    try:
        logger.info("Starting release note check...")

        current_version = await get_current_version()
        if not current_version:
            logger.warning("Current version not available, skipping release note")
            return

        current_sha = await get_tag_commit_sha(current_version)
        if not current_sha:
            logger.warning("Could not find commit SHA for version %s", current_version)
            return

        last_deployed_sha = await get_tag_commit_sha(LAST_DEPLOYED_TAG)
        if last_deployed_sha and last_deployed_sha == current_sha:
            logger.info("No new commits since last deployment")
            return

        commit_count = await get_commit_count_between(last_deployed_sha, current_sha)
        if last_deployed_sha and commit_count <= 0:
            logger.info("No new commits found")
            return

        tag_message = await get_tag_message(current_version)
        if not tag_message:
            tag_message = current_version

        release_note = format_release_note(current_version, tag_message, commit_count)
        logger.info("Generated release note:\n%s", release_note)

        published = await publish_release_note(release_note)
        logger.info("Release note published: %s\nContent:\n%s", published, release_note)

        github_token = await get_github_token()
        if github_token:
            await update_tag(LAST_DEPLOYED_TAG, current_sha, github_token)
        else:
            logger.warning("GitHub token not available, cannot update last-deployed tag")
            await _send_private_alert_once(
                "github-token-missing",
                "[release-note] GitHub token missing before update_tag. "
                "Set GITHUB_TOKEN for tag update.",
            )

        logger.info("Release note check completed")
    except Exception as exc:
        logger.error("Error in check_and_publish_release_note: %s", exc)


@driver.on_startup
async def on_startup() -> None:
    logger.info("Bot started, scheduling release note check...")
    from datetime import datetime, timedelta

    run_time = datetime.now() + timedelta(seconds=5)
    scheduler.add_job(
        check_and_publish_release_note,
        "date",
        run_date=run_time,
        id="release_note_check",
        replace_existing=True,
        misfire_grace_time=60,
    )


check_release = on_command("检查更新", permission=SUPERUSER, priority=5)


@check_release.handle()
async def handle_check_release() -> None:
    await check_release.send("开始检查版本更新...")
    await check_and_publish_release_note()
    await check_release.send("版本检查完成")

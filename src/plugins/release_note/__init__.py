import logging
from typing import Optional
import httpx

from nonebot import get_driver, require, on_command
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11 import Bot

logger = logging.getLogger(__name__)

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import plugin_config

__plugin_meta__ = PluginMetadata(
    name="release-note",
    description="自动发布版本更新日志",
    usage="Bot启动时自动检查版本更新并发布",
)

# 配置
GITHUB_API_BASE = f"https://api.github.com/repos/{plugin_config.github_repo_owner}/{plugin_config.github_repo_name}"
NAPCAT_API_BASE = plugin_config.napcat_api_base
LAST_DEPLOYED_TAG = plugin_config.last_deployed_tag

driver = get_driver()


async def get_github_token() -> Optional[str]:
    """从环境变量获取GitHub Token"""
    token = plugin_config.github_token
    if not token:
        logger.warning("GITHUB_TOKEN not found in configuration")
    return token


async def get_current_version() -> Optional[str]:
    """从环境变量获取当前版本"""
    version = plugin_config.version
    if not version:
        logger.warning("VERSION not found in configuration")
    return version


async def get_tag_commit_sha(tag_name: str) -> Optional[str]:
    """获取指定tag的commit SHA"""
    try:
        async with httpx.AsyncClient() as client:
            # 先尝试获取tag信息
            response = await client.get(
                f"{GITHUB_API_BASE}/git/refs/tags/{tag_name}",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                # tag可能是annotated tag或lightweight tag
                if data["object"]["type"] == "tag":
                    # Annotated tag，需要再获取tag对象
                    tag_response = await client.get(
                        data["object"]["url"],
                        headers={"Accept": "application/vnd.github.v3+json"},
                        timeout=30.0
                    )
                    if tag_response.status_code == 200:
                        return tag_response.json()["object"]["sha"]
                else:
                    # Lightweight tag，直接返回commit SHA
                    return data["object"]["sha"]
            elif response.status_code == 404:
                logger.info(f"Tag {tag_name} not found")
                return None
            else:
                logger.error(f"Failed to get tag {tag_name}: {response.status_code}")
                return None
                
    except Exception as e:
        logger.error(f"Error getting tag commit SHA: {e}")
        return None


async def get_commits_between(base_sha: Optional[str], head_sha: str) -> list[dict]:
    """获取两个commit之间的所有commits"""
    try:
        async with httpx.AsyncClient() as client:
            if base_sha:
                # 比较两个commits
                response = await client.get(
                    f"{GITHUB_API_BASE}/compare/{base_sha}...{head_sha}",
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=30.0
                )
            else:
                # 如果没有base，获取最近的commits
                response = await client.get(
                    f"{GITHUB_API_BASE}/commits",
                    params={"sha": head_sha, "per_page": 10},
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=30.0
                )
            
            if response.status_code == 200:
                data = response.json()
                if base_sha and "commits" in data:
                    return data["commits"]
                elif not base_sha:
                    return data
                else:
                    return []
            else:
                logger.error(f"Failed to get commits: {response.status_code}")
                return []
                
    except Exception as e:
        logger.error(f"Error getting commits: {e}")
        return []


async def get_version_tags_at_commit(commit_sha: str) -> list[str]:
    """获取指定commit上的所有版本号tag（不包括LAST_DEPLOYED_TAG）"""
    try:
        async with httpx.AsyncClient() as client:
            # 获取所有指向该commit的tag
            response = await client.get(
                f"{GITHUB_API_BASE}/git/refs/tags",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=30.0
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to get tags: {response.status_code}")
                return []
            
            all_tags = response.json()
            version_tags = []
            
            # 检查每个tag是否指向该commit
            for tag_ref in all_tags:
                tag_name = tag_ref["ref"].replace("refs/tags/", "")
                
                # 跳过last-deployed tag
                if tag_name == LAST_DEPLOYED_TAG:
                    continue
                
                # 获取该tag的commit SHA
                tag_response = await client.get(
                    tag_ref["url"],
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=30.0
                )
                
                if tag_response.status_code == 200:
                    tag_data = tag_response.json()
                    # 处理annotated tag和lightweight tag
                    tag_commit_sha = None
                    if tag_data["object"]["type"] == "tag":
                        # Annotated tag，获取指向的commit
                        annotation_response = await client.get(
                            tag_data["object"]["url"],
                            headers={"Accept": "application/vnd.github.v3+json"},
                            timeout=30.0
                        )
                        if annotation_response.status_code == 200:
                            tag_commit_sha = annotation_response.json()["object"]["sha"]
                    else:
                        # Lightweight tag，直接是commit
                        tag_commit_sha = tag_data["object"]["sha"]
                    
                    # 如果该tag指向我们要找的commit，添加到列表
                    if tag_commit_sha == commit_sha:
                        version_tags.append(tag_name)
            
            return version_tags
                
    except Exception as e:
        logger.error(f"Error getting version tags at commit: {e}")
        return []


async def update_tag(tag_name: str, commit_sha: str, token: str) -> bool:
    """更新或创建tag到指定commit"""
    try:
        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {token}",
            }
            
            # 先删除旧tag（如果存在）
            delete_response = await client.delete(
                f"{GITHUB_API_BASE}/git/refs/tags/{tag_name}",
                headers=headers,
                timeout=30.0
            )
            
            if delete_response.status_code in [204, 404]:
                # 创建新tag
                create_response = await client.post(
                    f"{GITHUB_API_BASE}/git/refs",
                    headers=headers,
                    json={
                        "ref": f"refs/tags/{tag_name}",
                        "sha": commit_sha
                    },
                    timeout=30.0
                )
                
                if create_response.status_code == 201:
                    logger.info(f"Successfully updated tag {tag_name} to {commit_sha}")
                    return True
                else:
                    logger.error(f"Failed to create tag: {create_response.status_code}, {create_response.text}")
                    return False
            else:
                logger.error(f"Failed to delete old tag: {delete_response.status_code}")
                return False
                
    except Exception as e:
        logger.error(f"Error updating tag: {e}")
        return False


async def publish_release_note(release_note: str) -> bool:
    """通过NapCat API发布release note到个人签名"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{NAPCAT_API_BASE}/set_self_longnick",
                headers={"content-type": "application/json"},
                json={"longNick": release_note},
                timeout=30.0
            )
            
            if response.status_code == 200:
                logger.info("Successfully published release note")
                return True
            else:
                logger.error(f"Failed to publish release note: {response.status_code}")
                return False
                
    except Exception as e:
        logger.error(f"Error publishing release note: {e}")
        return False


def format_release_note(commits: list[dict], old_version: Optional[str], new_version: str) -> str:
    """格式化release note"""
    if not commits:
        return f"版本 {new_version} 已部署，无新提交"
    
    # 构建release note
    lines = []
    if old_version:
        lines.append(f"🚀 版本更新: {old_version} → {new_version}")
    else:
        lines.append(f"🚀 版本 {new_version} 已部署")
    
    lines.append("")
    lines.append("📝 更新内容:")
    
    # 添加commit messages
    max_display = plugin_config.max_commits_display
    max_length = plugin_config.max_message_length
    
    for i, commit in enumerate(commits[:max_display]):
        message = commit["commit"]["message"].split("\n")[0]  # 只取第一行
        # 截断过长的消息
        if len(message) > max_length:
            message = message[:max_length - 3] + "..."
        lines.append(f"  • {message}")
    
    if len(commits) > max_display:
        lines.append(f"  ... 以及其他 {len(commits) - max_display} 个更新")
    
    return "\n".join(lines)


async def check_and_publish_release_note():
    """检查版本并发布release note"""
    try:
        logger.info("Starting release note check...")
        
        # 获取当前版本
        current_version = await get_current_version()
        if not current_version:
            logger.warning("Current version not available, skipping release note")
            return
        
        # 获取当前版本的commit SHA
        current_sha = await get_tag_commit_sha(current_version)
        if not current_sha:
            logger.warning(f"Could not find commit SHA for version {current_version}")
            return
        
        # 获取上次部署的版本
        last_deployed_sha = await get_tag_commit_sha(LAST_DEPLOYED_TAG)
        
        # 如果两个SHA相同，说明没有更新
        if last_deployed_sha and last_deployed_sha == current_sha:
            logger.info("No new commits since last deployment")
            return
        
        # 获取两个版本之间的commits
        commits = await get_commits_between(last_deployed_sha, current_sha)
        
        if not commits and last_deployed_sha:
            logger.info("No new commits found")
            return
        
        # 获取旧版本号（用于显示）
        old_version = None
        if last_deployed_sha:
            # 从last-deployed tag指向的commit上获取版本号tag
            version_tags = await get_version_tags_at_commit(last_deployed_sha)
            if version_tags:
                # 优先选择看起来最像版本号的tag（通常是最后一个或包含v的）
                old_version = version_tags[0]
                logger.info(f"Found old version tags: {version_tags}, using: {old_version}")
            else:
                old_version = "previous"
        
        # 格式化并发布release note
        release_note = format_release_note(commits, old_version, current_version)
        logger.info(f"Generated release note:\n{release_note}")
        
        # 发布到个人签名
        published = await publish_release_note(release_note)
        
        if published:
            # 更新last-deployed tag
            github_token = await get_github_token()
            if github_token:
                await update_tag(LAST_DEPLOYED_TAG, current_sha, github_token)
            else:
                logger.warning("GitHub token not available, cannot update last-deployed tag")
        
        logger.info("Release note check completed")
        
    except Exception as e:
        logger.error(f"Error in check_and_publish_release_note: {e}")


# Bot启动时检查
@driver.on_startup
async def on_startup():
    """Bot启动时触发"""
    logger.info("Bot started, scheduling release note check...")
    # 延迟5秒执行，确保bot完全就绪
    from datetime import datetime, timedelta
    run_time = datetime.now() + timedelta(seconds=5)
    
    scheduler.add_job(
        check_and_publish_release_note,
        "date",
        run_date=run_time,
        id="release_note_check",
        replace_existing=True,
        misfire_grace_time=60
    )


# 手动触发命令（仅超级用户）
check_release = on_command("检查更新", permission=SUPERUSER, priority=5)

@check_release.handle()
async def handle_check_release():
    """手动触发release note检查"""
    await check_release.send("开始检查版本更新...")
    await check_and_publish_release_note()
    await check_release.send("版本检查完成")

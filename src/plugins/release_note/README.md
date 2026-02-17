# Release Note Plugin

自动发布版本更新日志的 NoneBot 插件。

## 功能

- Bot 启动时自动检查版本更新
- 比较当前版本与上次部署版本的差异
- 获取两个版本之间的所有 commit 信息
- 将更新日志发布到 QQ 个人签名
- 自动更新远程仓库的 `last-deployed` tag
- GitHub token 失效/权限异常时私聊告警 `SUPERUSERS[0]`

## 配置

需要在 `.env` 文件中添加以下环境变量：

```env
# 当前版本号（通常由 CI/CD 设置）
VERSION=v1.2.11

# 超级用户（私聊告警会使用第一个）
SUPERUSERS=["1669790626"]

# GitHub Personal Access Token（需要 repo 权限）
GITHUB_TOKEN=ghp_your_token_here

# 可选配置（有默认值）
GITHUB_REPO_OWNER=trytodupe
GITHUB_REPO_NAME=ttd-bot
LAST_DEPLOYED_TAG=last-deployed
NAPCAT_API_BASE=http://127.0.0.1:3000
MAX_COMMITS_DISPLAY=10
MAX_MESSAGE_LENGTH=60
```

## 工作流程

1. Bot 启动并连接时触发检查
2. 从环境变量获取当前版本 `VERSION`
3. 从 GitHub API 获取 `last-deployed` tag 的 commit SHA
4. 比较两个版本，获取之间的所有 commits
5. 格式化 release note 并发布到 QQ 个人签名
6. 更新远程仓库的 `last-deployed` tag 到当前版本

## 手动触发

超级用户可以使用命令手动触发检查：

```
/检查更新
```

## 异常告警

- 当 GitHub API 返回 token/权限异常（典型是 401/403 + `Bad credentials` 等）时，插件会私聊 `SUPERUSERS[0]`。
- 插件进程内对该类告警做去重，避免单次启动期间重复刷屏。
- 该告警逻辑在 `release_note` 插件内实现，不依赖 `./deploy.sh`。

## API 端点

- GitHub API: `https://api.github.com/repos/trytodupe/ttd-bot/...`
- NapCat API: `http://127.0.0.1:3000/set_self_longnick`

## 依赖

- `nonebot_plugin_apscheduler`: 用于任务调度
- `httpx`: 用于 HTTP 请求

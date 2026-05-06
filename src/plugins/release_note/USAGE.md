# Release Note Plugin - 使用指南

## 快速开始

### 1. 配置环境变量

在 `.env` 文件中添加：

```env
VERSION=v1.2.11
SUPERUSERS=["1669790626"]
GITHUB_TOKEN=ghp_your_github_token_here
```

### 2. 获取 GitHub Token

1. 访问 GitHub Settings → Developer settings → Personal access tokens
2. 点击 "Generate new token (classic)"
3. 勾选 `repo` 权限（用于读取和修改 tag）
4. 生成并复制 token
5. 将 token 添加到 `.env` 文件

### 3. 启动 Bot

插件会在 Bot 启动后自动运行：

1. Bot 连接成功后延迟 5 秒执行检查
2. 比较当前 VERSION 与 `last-deployed` tag
3. 读取当前 deploy tag 的中文 message，生成 `<version>: <tag message> (+N)` 形式的 release note
4. 更新远程仓库的 `last-deployed` tag

### 4. GitHub token 异常私聊告警

- 当 GitHub 返回 token/权限异常时（常见为 401/403），插件会私聊 `SUPERUSERS[0]`。
- 同一次进程生命周期内，该异常只会告警一次，避免重复刷屏。
- 告警逻辑在插件内部实现，不依赖 `deploy.sh`。

## 工作原理

### 版本检查流程

```
Bot 启动
  ↓
延迟 5 秒
  ↓
获取 VERSION 环境变量
  ↓
获取 last-deployed tag 的 commit SHA
  ↓
比较两个 SHA
  ↓
相同？ → 跳过（无更新）
  ↓
不同 → 获取中间的 commits
  ↓
格式化 release note
  ↓
发布到 QQ 个人签名
  ↓
更新 last-deployed tag
```

### Release Note 格式

```
v1.4.18: 更新 moellmchats 依赖版本 (+7)
```

## 手动触发

超级用户可以使用命令手动触发检查：

```
检查更新
```

这将立即执行版本检查和发布流程。

## 调试

查看日志输出：

```bash
# 启动时的日志
Bot connected, scheduling release note check...
Starting release note check...

# 成功发布
Generated release note:
🚀 版本更新: v1.2.10 → v1.2.11
...
Successfully published release note
Successfully updated tag last-deployed to abc123...
Release note check completed

# 无更新
No new commits since last deployment
```

## 常见问题

### Q: 如何初始化 `last-deployed` tag？

A: 首次使用时，用发布脚本创建带中文 message 的 annotated tag。不要直接手工 `git tag`，否则后续 release note 没有稳定的主输入。

### Q: GitHub Token 权限不足？

A: 确保 token 有 `repo` 权限，特别是可以创建和删除 tags。

### Q: token 过期时会有 QQ 提示吗？

A: 会。插件会私聊 `SUPERUSERS[0]`，并在日志中保留对应的 GitHub 响应状态。

### Q: Release note 没有发布到 QQ？

A: 检查 NapCat API 是否正常运行：
```bash
curl http://127.0.0.1:3000/get_login_info
```

### Q: 如何自定义 release note 格式？

A: 修改 `__init__.py` 中的 `format_release_note()` 函数，但发布侧 tag message 仍需先由脚本生成并控制在 80 字符内，且统一使用中文。

## CI/CD 集成

在 CI/CD 流程中设置 VERSION 环境变量：

### GitHub Actions 示例

```yaml
- name: Deploy
  env:
    VERSION: ${{ github.ref_name }}
  run: |
    docker-compose up -d
```

### Docker Compose 示例

```yaml
services:
  bot:
    environment:
      - VERSION=${VERSION:-v1.0.0}
      - GITHUB_TOKEN=${GITHUB_TOKEN}
```

## 进阶配置

在 `.env` 中可以自定义更多参数：

```env
# GitHub 仓库配置
GITHUB_REPO_OWNER=trytodupe
GITHUB_REPO_NAME=ttd-bot

# Tag 名称
LAST_DEPLOYED_TAG=last-deployed

# NapCat API 地址
NAPCAT_API_BASE=http://127.0.0.1:3000

# Release note 格式
MAX_COMMITS_DISPLAY=10      # 最多显示多少个 commits
MAX_MESSAGE_LENGTH=60       # commit message 最大长度
```

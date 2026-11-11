---
updated: 2026-11-11T16:14:17+08:00
status: active
milestone: 抖音提取器修复已本地验证，等待 CI 部署与生产验收
last_verified: 2026-11-11T16:13:00+08:00
---

# audio-wiki-api — Handoff

## 当前状态

- 当前目标：修复抖音链接因平台新签名机制而误报 `Fresh cookies ... are needed`。
- 已完成：将生产镜像基线改为 Python 3.11，并将 yt-dlp 固定到上游修复 PR 的提交 `728fe46fbaa97d6f03efcaf9e766d4db63338b8d`。
- 进行中：等待提交、GitHub Actions 构建部署和阿里云 FC 生产验证。
- 阻塞：本机没有 Docker CLI，容器构建必须由 GitHub Actions 验证。

## 已完成并验证

| 事项 | 结果 | 证据 |
|---|---|---|
| 失败根因 | 新鲜且结构完整的 Cookie 仍被旧提取器误报；不是 URL 提取或 Cookie 格式问题 | 生产任务 `codex-douyin-retry-20261111-02` 的 FC 日志 |
| Python 运行时 | 依赖可在 Python 3.12 隔离环境安装；生产 Dockerfile 已改为 Python 3.11 | `Dockerfile`、临时隔离环境安装结果 |
| yt-dlp 固定版本 | PR 提交构建为 `2026.10.26`，包含 `_sign_query` 和 `_get_uifid` | `requirements.txt`；运行时自检 |
| 回归测试 | 47 项测试全部通过 | `/private/tmp/audio-wiki-douyin-fix-venv/bin/python -m unittest -v test_main.py` |
| 语法检查 | `main.py`、`test_main.py` 编译通过 | `PYTHONPYCACHEPREFIX=/private/tmp/audio-wiki-douyin-fix-pycache ... -m py_compile ...` |
| 抖音真实链接静态验收 | 无 Cookie 元数据提取成功，得到视频 ID、标题和 252 秒时长 | `yt-dlp --simulate ... https://v.douyin.com/IFY_so3Xt3I/` |

## 尚未验证

- GitHub Actions 是否能用 Python 3.11 镜像完成 Docker 构建并更新 FC。
- 阿里云香港生产出口是否能下载、转写并将原抖音链接写入 OSS/Obsidian。
- 升级后的 yt-dlp 对真实小红书视频和 B 站链接是否无回归。

## 阻塞与需要决策

- 本机没有 Docker CLI，容器构建需由 GitHub Actions 验证；无需额外人工决策。

## 下一步行动

1. 提交并推送 `Dockerfile`、`requirements.txt` 和文档更新。
2. 确认 GitHub Actions 成功，FC `lastUpdateStatus=Successful` 且镜像为新提交。
3. 重提 `https://v.douyin.com/IFY_so3Xt3I/`，轮询到 `succeeded` 并确认生成文件。
4. 用已授权的真实小红书视频和 B 站链接各做一次回归验证。

## 关键文件

- `Dockerfile`：Python 3.11 生产镜像。
- `requirements.txt`：固定 yt-dlp 抖音修复提交。
- `main.py`：平台下载、转写和任务状态逻辑。
- `test_main.py`：URL、Cookie、小红书与异步任务回归测试。

## 验证命令与期望结果

```text
命令：python3 -m unittest -v test_main.py
期望：47 项测试通过。

命令：python3 -m py_compile main.py test_main.py
期望：退出码 0。

线上检查：POST 返回 202；GET /api/tasks/{task_id} 最终返回 succeeded；FC 日志不再出现 Fresh cookies 错误。
验证边界：本地元数据成功不等于生产下载、ASR、OSS 写入和设备端通知已验收。
```

## 千万别做

- 不要把 `DOUYIN_COOKIES`、其他 Cookie 或云端凭证写入仓库或日志。
- 不要把本地 `--simulate` 成功当作阿里云生产链路成功。
- 不要将 yt-dlp 改回未包含抖音签名修复的正式版本后直接部署。

## 回退

若新镜像部署或生产验证失败，将 FC 镜像回退到提交 `1657a338c6d7fa07824058b44d75b65f5f978fbc` 对应的 ACR 标签。

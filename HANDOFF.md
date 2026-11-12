---
updated: 2026-11-12T13:34:31+08:00
status: active
milestone: 抖音提取器修复已部署并通过生产端到端验收
last_verified: 2026-11-12T13:33:20+08:00
---

# audio-wiki-api — Handoff

## 当前状态

- 当前目标：修复抖音链接因平台新签名机制而误报 `Fresh cookies ... are needed`。
- 已完成：Python 3.11 镜像和 yt-dlp 修复提交已部署；原抖音链接完成下载、转写和 OSS/Obsidian 写入。
- 进行中：等待真实小红书视频和 B 站链接的回归验证。
- 阻塞：无。

## 已完成并验证

| 事项 | 结果 | 证据 |
|---|---|---|
| 失败根因 | 新鲜且结构完整的 Cookie 仍被旧提取器误报；不是 URL 提取或 Cookie 格式问题 | 生产任务 `codex-douyin-retry-20261111-02` 的 FC 日志 |
| Python 运行时 | 依赖可在 Python 3.12 隔离环境安装；生产 Dockerfile 已改为 Python 3.11 | `Dockerfile`、临时隔离环境安装结果 |
| yt-dlp 固定版本 | PR 提交构建为 `2026.10.26`，包含 `_sign_query` 和 `_get_uifid` | `requirements.txt`；运行时自检 |
| 回归测试 | 47 项测试全部通过 | `/private/tmp/audio-wiki-douyin-fix-venv/bin/python -m unittest -v test_main.py` |
| 语法检查 | `main.py`、`test_main.py` 编译通过 | `PYTHONPYCACHEPREFIX=/private/tmp/audio-wiki-douyin-fix-pycache ... -m py_compile ...` |
| 抖音真实链接静态验收 | 无 Cookie 元数据提取成功，得到视频 ID、标题和 252 秒时长 | `yt-dlp --simulate ... https://v.douyin.com/IFY_so3Xt3I/` |
| CI 与部署 | GitHub Actions 成功，FC 为 `Active / Successful` 并使用新镜像 | Actions run `42985709717`；镜像提交 `29b2650bcb1d12dab23d4296c59decd377a2cd28` |
| 抖音生产验收 | 最终 `succeeded`，已保存 Markdown 笔记 | 任务 `codex-douyin-fix-20261112-01` |

## 尚未验证

- 升级后的 yt-dlp 对真实小红书视频和 B 站链接是否无回归。

## 阻塞与需要决策

- 无当前阻塞；上游 PR 尚未合并，后续升级 yt-dlp 时需重新判断是否可回归正式版。

## 下一步行动

1. 用已授权的真实小红书视频和 B 站链接各做一次回归验证。
2. 关注上游 yt-dlp PR #17826；合并并发布后再评估回归正式版本。

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

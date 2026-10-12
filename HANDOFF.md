---
updated: 2026-10-12T13:58+08:00
status: active
milestone: 异步任务轮询已完成本地实现，等待线上验证
last_verified: 2026-10-12T13:58+08:00
---

# audio-wiki-api — Handoff

## 当前状态

- 当前目标：解决小红书多图 OCR 导致 iOS 快捷指令等待超时的问题，同时保留最终完成反馈。
- 已完成：任务状态写入 OSS、状态查询接口、快捷指令轮询文档和兼容测试。
- 进行中：推送部署并验证真实异步请求从 `queued/processing` 到最终状态。
- 阻塞：无。

## 已完成并验证

| 事项 | 结果 | 证据 |
|---|---|---|
| 最新线上代码基线 | 本地已快进到 `origin/main` 的 `875c4bd` 后开发 | `git log -1 origin/main` |
| 任务状态模型 | 支持 `queued/processing/succeeded/failed` | `main.py` 的 `save_task_status`、`get_task_status` |
| 快捷指令协议 | UUID + FC 异步请求头 + 5 秒轮询 | `docs/rednote-shortcut.md` |
| 回归测试 | 新增任务跟踪及原有小红书分支 7 项定向测试通过 | README 中的测试命令边界说明 |
| 语法检查 | `main.py`、`test_main.py` 编译通过 | `PYTHONPYCACHEPREFIX=/tmp/... python3 -m py_compile ...` |

## 尚未验证

- GitHub Actions 构建及 FC 新镜像部署。
- 真实异步提交是否快速返回 `202`，以及轮询是否最终返回文件名。
- iPhone 快捷指令的实际菜单、循环和停止分支，需要在设备上配置后确认。

## 阻塞与需要决策

- 无代码或部署阻塞。设备端验收需要用户按 `docs/rednote-shortcut.md` 更新快捷指令。

## 下一步行动

1. 提交并推送代码；确认 GitHub Actions 成功。
2. 等待 FC `lastUpdateStatus=Successful`。
3. 用唯一 `task_id` 提交真实异步请求并轮询到终态。
4. 按 `docs/rednote-shortcut.md` 更新 iOS 快捷指令并做设备验收。

## 关键文件

- `main.py`：任务状态存取、跟踪执行和状态查询接口。
- `test_main.py`：任务跟踪与兼容测试。
- `docs/rednote-shortcut.md`：快捷指令配置。
- `README.md`：稳定接口和运行说明。

## 验证命令与期望结果

```text
命令：python3 -m unittest -v test_main.py
期望：Docker/CI 的 Python 3.10 环境全部通过；本机 Python 3.9 的两个既有环境失败见 README。

命令：env PYTHONPYCACHEPREFIX=/tmp/audio-wiki-pycache python3 -m py_compile main.py test_main.py
期望：退出码 0。

线上检查：异步 POST 返回 202；随后 GET /api/tasks/{task_id} 最终返回 succeeded 或 failed。
验证边界：设备端快捷指令交互仍需在 iPhone 上验收。
```

## 千万别做

- 不要恢复“HTTP 返回后继续 daemon 线程”的小红书方案；FC 可能回收实例。
- 不要把 OSS/平台凭证、Cookie 或线上日志原文写入项目文档。
- 不要删除未跟踪的 `.claude/` 或 `oss_lifecycle_cleanup.xml`。

## 回退

代码通过 Git 回退对应提交；任务状态位于 `temp_audio/task_status/`，回退代码不要求删除已有状态对象。

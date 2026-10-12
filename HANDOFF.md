---
updated: 2026-10-12T17:59+08:00
status: active
milestone: 异步请求校验失败可通过任务状态返回，并完成线上验证
last_verified: 2026-10-12T17:58+08:00
---

# audio-wiki-api — Handoff

## 当前状态

- 当前目标：解决小红书多图 OCR 导致 iOS 快捷指令等待超时的问题，同时保留最终完成反馈。
- 已完成：任务状态写入 OSS、状态查询接口、按 `message` 判断完成，以及异步参数校验失败的状态回传、部署和线上验证。
- 进行中：等待在 iPhone 上按文档更新快捷指令并确认设备交互。
- 阻塞：无。

## 已完成并验证

| 事项 | 结果 | 证据 |
|---|---|---|
| 最新线上代码基线 | 参数校验失败状态回传已部署 | 提交 `dd855d2` |
| 任务状态模型 | 支持 `queued/processing/succeeded/failed` | `main.py` 的 `save_task_status`、`get_task_status` |
| 快捷指令协议 | UUID + FC 异步请求头 + 5 秒轮询 | `docs/rednote-shortcut.md` |
| 回归测试 | 新增任务跟踪及原有小红书分支 7 项定向测试通过 | README 中的测试命令边界说明 |
| 语法检查 | `main.py`、`test_main.py` 编译通过 | `PYTHONPYCACHEPREFIX=/tmp/... python3 -m py_compile ...` |
| 生产部署 | GitHub Actions 成功，FC 镜像为 `dd855d2` | Actions run `38232794287`；FC `lastUpdateStatus=Successful` |
| 异步提交 | POST 在 1.84 秒返回 `202` | 测试任务 `codex-poll-affb8da-02` |
| 状态轮询 | `processing` 最终变为 `succeeded` 并返回文件名 | 线上 `GET /api/tasks/{task_id}` |
| 简化完成判断 | `queued/processing` 不含 `message`；成功或失败才返回 `message` | 提交 `6840c8a`；Actions run `38220057649` |
| 简化轮询线上验证 | POST 1.93 秒返回 `202`；`processing` 无 `message`；最终 `succeeded` 且含保存通知 | 测试任务 `codex-message-6840c8a-01`；临时状态对象已清理 |
| 参数校验失败回传 | 合法 `task_id` 的校验错误写为 `failed`，不再永久停留在 `queued` | 线上错误类型测试返回具体布尔值提示；临时状态对象已清理 |

## 尚未验证

- iPhone 快捷指令的实际菜单、循环和停止分支，需要在设备上配置后确认。

## 阻塞与需要决策

- 无代码或部署阻塞。设备端验收需要用户按 `docs/rednote-shortcut.md` 更新快捷指令。

## 下一步行动

1. 将快捷指令字典中的 `save_images`、`enable_ocr` 类型确认设为“布尔值”，重新提交真实链接。
2. 确认成功或失败的 `message` 均会触发通知并停止循环，处理中会继续轮询。

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

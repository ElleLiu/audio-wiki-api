# audio-wiki-api

将 B 站、小红书、抖音及网页链接转换为 Obsidian Markdown 笔记。iOS
快捷指令提交链接，阿里云函数计算（FC）负责下载、转写或抓取，结果写入 OSS，
再由 Obsidian Remotely Save 同步。

## 处理流程

- 音视频：下载音频 -> Fun-ASR -> DeepSeek 整理 -> Markdown。
- 小红书图文：保存原始正文；按请求选项保存压缩图片和/或 OCR，不调用 DeepSeek。
- 长任务：快捷指令使用 FC 异步调用并携带 `task_id`，随后轮询任务状态。

## 关键接口

`POST /api/process`

```json
{
  "url": "https://xhslink.cn/o/example",
  "task_id": "由快捷指令生成的 UUID",
  "save_images": true,
  "enable_ocr": true
}
```

异步提交时增加请求头 `X-Fc-Invocation-Type: Async`。FC 接受任务后返回空的
`202 Accepted`。

`GET /api/tasks/{task_id}`

返回 `queued`、`processing`、`succeeded` 或 `failed`。成功响应包含最终
Markdown 文件名。完整快捷指令配置见
[`docs/rednote-shortcut.md`](docs/rednote-shortcut.md)。

## 项目结构

- `main.py`：FastAPI 入口与全部处理逻辑。
- `test_main.py`：单元测试。
- `Dockerfile`、`requirements.txt`：FC 自定义容器环境。
- `.github/workflows/deploy.yml`：推送 `main` 后构建镜像并更新 FC。
- `docs/`：专项操作说明。
- `inputs/`、`code/`、`outputs/`、`work/`：新任务材料的标准目录；现有源码保持原位置。

## 本地验证

```bash
python3 -m unittest -v test_main.py
env PYTHONPYCACHEPREFIX=/tmp/audio-wiki-pycache python3 -m py_compile main.py test_main.py
```

本机系统 Python 3.9 未安装 Pillow，且不支持项目既有测试使用的
`int.bit_count()`；完整测试需在安装 `requirements.txt` 的 Python 3.10+
环境运行。

## 部署与排障

推送 `main` 会自动部署。部署后用 `./fetch_logs.sh 30` 查看最近 30 分钟 FC
日志。环境变量名称与安全要求见 `AGENTS.md`，不得把 Cookie、API Key 或
AccessKey 写入仓库。

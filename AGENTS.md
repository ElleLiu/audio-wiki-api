# audio-wiki-api 项目背景

## 项目目标

把音视频链接（B站、小红书、抖音等）通过分享/复制 URL 触发 iOS 快捷指令，自动完成：
**下载音频 → ASR 转写 → LLM 润色为结构化 Markdown 笔记 → 存入 OSS → 同步到 Obsidian**

## 技术架构

- **触发端**：iOS 快捷指令（共享表单 + 剪贴板双入口，POST 到 FC API）
- **计算层**：阿里云函数计算 FC（cn-hongkong 区域，自定义容器镜像）
- **存储层**：阿里云 OSS（`obsidian-remotely-1121` Bucket，香港区域）
- **ASR**：阿里云百炼 Fun-ASR（Paraformer-zh + FSMN-VAD + CAM++ + CT-Punc）
- **LLM 润色**：DeepSeek（`deepseek-v4-flash`）
- **同步**：Obsidian + Remotely Save 插件 → OSS（S3 兼容协议）
- **CI/CD**：GitHub Actions（push main → 构建镜像推ACR → 自动更新FC）

## 仓库地址

https://github.com/ElleLiu/audio-wiki-api

关键文件：
- `main.py` — FastAPI 主程序，所有业务逻辑在这里
- `Dockerfile` — Python 3.10-slim 基础镜像 + FFmpeg
- `requirements.txt` — 依赖

## API 接口

`POST /api/process`

请求体（JSON）：
```json
{
  "url": "https://b23.tv/xxx",        // 字段名必须是 url，不是 video_url
  "original_url": "可选",
  "title": "可选",
  "text": "可选，直接传文本时使用"
}
```

返回：立即返回 `{"status": "accepted", ...}`，后台线程异步处理。

## 已解决的关键问题（按时间顺序）

### 1. FC 实例提前回收导致后台线程被杀

**症状**：日志显示"后台开始处理"后再无任何输出，任务静默失败。

**原因**：FC 最小实例数 = 0，返回响应后实例被立即回收，后台 `threading.Thread(daemon=True)` 跟着死亡。

**解决**：FC 配置 → 高级配置 → **延时释放弹性实例 = 600 秒**（和函数超时一致）。比设置最小实例数=1 更省钱。

### 2. ACR 镜像构建失败（Docker Hub 限流）

**症状**：ACR 构建日志报 `429 Too Many Requests - You have reached your unauthenticated pull rate limit`。

**原因**：ACR 共享 IP 触发了 Docker Hub 的匿名拉取限制（6 小时 100 次）。

**解决**：等几小时再触发构建，或换镜像源。注意：阿里云 `acs/library` 命名空间下没有 `python:3.10-slim`，不要乱换。

### 3. B站 412 Precondition Failed

**症状**：yt-dlp 下载 B站视频报 `HTTP Error 412`。

**原因**：阿里云香港 IP 被 B站反爬虫识别。

**解决**：在 `download_audio` 函数里加 cookie 文件，cookie 内容存为环境变量 `BILIBILI_COOKIES`（FastAPI 启动时写入 `/tmp/bilibili_cookies.txt`）。代码模式：

```python
cookie_path = "/tmp/bilibili_cookies.txt"
cookies_content = os.environ.get("BILIBILI_COOKIES", "")
if cookies_content and not os.path.exists(cookie_path):
    with open(cookie_path, "w") as f:
        f.write(cookies_content)

ydl_opts = {
    ...
    'cookiefile': cookie_path if os.path.exists(cookie_path) else None,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com/',
    },
}
```

### 4. yt-dlp 报 Unsupported URL: m.bilibili.com

**症状**：B站短链跳转后被解析为 `m.bilibili.com/video/...`（移动端域名），yt-dlp 不识别。

**原因**：UA 设置成了 iPhone，B站给跳到了移动端域名。

**解决**：把 User-Agent 改成 macOS Chrome（桌面端），B站会跳到 `www.bilibili.com`。

### 5. OSS 上传 SignatureDoesNotMatch

**症状**：音频下载成功后上传 OSS 报 403 SignatureDoesNotMatch。

**根本原因待确认**：可能是 AccessKey Secret 配置错误（带空格/换行），或文件名含全角字符（`｜`）导致签名计算错。

**解决方向**：
1. 迁移到 RAM 用户 AccessKey，给予 `AliyunOSSFullAccess`，更新环境变量
2. 在 `upload_audio_to_oss` 函数里做文件名清洗：
   ```python
   safe_basename = re.sub(r'[｜|<>:"/\\?*]', '_', basename)
   ```

### 6. iOS 快捷指令兼容小红书等无法分享的 App

**解决**：快捷指令开头加"如果没有输入：获取剪贴板"分支，让 URL 既能从共享表单进来，也能从剪贴板进来。

### 7. Remotely Save 同步警告（>=50% 变更）

**症状**：同步时报"3/4=75% 文件会被修改或删除"。

**原因**：基数太小（云端 4 个、本地 3 个文件），任何变化都容易超 50% 阈值。

**解决**：设置里调高允许变更比例（如 80%）。建议把 `temp_audio/` 路径加入 Remotely Save 排除列表，防止临时音频污染 vault。

## 已解决问题（续）

### 8. DeepSeek 输出开头有废话导致 frontmatter 失效

**症状**：笔记属性（title/date/tags）在 Obsidian 中显示为普通文本而非 YAML frontmatter。

**原因**：DeepSeek 在 `---` 之前输出"好的，作为…"之类的说明文字，导致 Obsidian 无法识别 frontmatter。

**解决**：
1. system prompt 明确禁止前置文字："第一行必须是 ---"
2. 代码里后处理裁掉第一个 `---` 之前的所有内容

### 9. 长音频字稿截断 + 按时长分路处理

**策略**（阈值 15 分钟 = 900 秒）：
- **≤15 分钟**：LLM 完整还原逐字稿（仅修标点/错别字）
- **>15 分钟**：LLM 只做结构化笔记 + 节选精彩片段与金句

**时长获取**：yt-dlp `info['duration']` 为 0 时用 `ffprobe` 从文件读取兜底。日志会打印 `⏱️ 音频时长: XX 分 XX 秒`。

### 10. 单人音频误加 Speaker 标签

**解决**：先统计唯一说话人数量，只有 >1 人时才加 `**Speaker X**:` 标签。

### 11. GitHub Actions 更新 FC 时意外清空环境变量

**原因**：`aliyun fc UpdateFunction --body` 里带了 `environmentVariables` 字段，FC 会用该字段**完全替换**原有的所有环境变量。

**解决**：`--body` 只传 `customContainerConfig`，不要包含 `environmentVariables`。如果需要更新环境变量，必须先 GetFunction 获取全量再合并。

## 待解决问题

### P1：抖音视频下载失败

**待获取**：具体的 FC 日志报错信息。

**预判**：抖音短链可能需要 cookie，或需升级 yt-dlp。

### P2：知乎登录墙

**如失败**：在 FC 环境变量里配置 `ZHIHU_COOKIE`（从浏览器导出知乎登录态 Cookie）。

## 环境变量清单

FC 函数当前需要的环境变量：

| 变量名 | 说明 |
|--------|------|
| `DASHSCOPE_API_KEY` | 阿里云百炼 Fun-ASR API Key |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `OSS_ACCESS_KEY_ID` | 建议用 RAM 用户的 Key |
| `OSS_ACCESS_KEY_SECRET` | 建议用 RAM 用户的 Secret |
| `OSS_BUCKET_NAME` | 默认 `obsidian-remotely-1121` |
| `OSS_ENDPOINT` | 默认 `https://oss-cn-hongkong.aliyuncs.com` |
| `DASHSCOPE_BASE_URL` | 默认 `https://dashscope.aliyuncs.com/api/v1` |
| `BILIBILI_COOKIES` | Netscape 格式的 cookie 文件全文，从浏览器插件导出 |
| `ZHIHU_COOKIE` | 知乎登录态 Cookie 字符串（可选，未设置时知乎内容可能触发登录墙）|

## 安全事项

- **AccessKey 务必用 RAM 用户**，权限最小化（只给 OSS 权限），不要用主账号 Key
- **BILIBILI_COOKIES 含登录态**，泄露等于账号被盗，注意不要 commit 到 GitHub
- FC HTTP 触发器是公网可访问的，会持续被网络扫描（GET /、GET /favicon.ico 等），返回 404 是正常背景噪音，不是攻击

## 部署流程（全自动）

1. 改代码 → `git push origin main`
2. GitHub Actions 自动触发（`.github/workflows/deploy.yml`）：
   - 构建 Docker 镜像，推送到 ACR（同时打 `latest` 和 commit SHA 两个 tag）
   - 调用阿里云 CLI 更新 FC 函数到新镜像
3. 约 3-5 分钟后 FC 自动使用新镜像，无需手动操作

**ACR 构建规则说明**：ACR 仅在推 `release-v*` tag 时触发构建（用于打正式版本镜像），不与 GitHub Actions 冲突。日常开发走 GitHub Actions 即可，无需改 ACR 配置。

**GitHub Secrets 需配置**（仓库 Settings → Secrets and variables → Actions）：

| Secret 名 | 值 |
|-----------|-----|
| `ACR_USERNAME` | 阿里云账号用户名（ACR 访问凭证里的登录名）|
| `ACR_PASSWORD` | ACR 访问凭证密码（在 ACR 控制台 → 访问凭证 里设置）|
| `ALIYUN_ACCESS_KEY_ID` | RAM 用户 AccessKey ID |
| `ALIYUN_ACCESS_KEY_SECRET` | RAM 用户 AccessKey Secret |

## 调试技巧

- **本地拉日志**：项目根目录有 `fetch_logs.sh`（已加入 .gitignore，不提交）
  ```bash
  ./fetch_logs.sh        # 最近 30 分钟
  ./fetch_logs.sh 60     # 最近 1 小时
  ```
- FC 日志里的关键标志：`🚀 收到请求` → `🔄 后台开始处理` → `⏱️ 音频时长` → `✅ 转写完成` → `✅ 全部完成`
- 加 `print(f"🍪 ...")` 这类带 emoji 的日志便于在长日志里搜索定位
- 后台异步任务的错误只能在 FC 日志里看，快捷指令端永远只看到 "已收到"
- aliyun CLI 已配置在本地（`~/.aliyun/config.json`），SLS 插件已安装

import os
import time
import threading
import requests
import yt_dlp
import oss2
from datetime import datetime
from fastapi import FastAPI
from openai import OpenAI
from dotenv import load_dotenv

# ================= 1. 初始化配置 =================
load_dotenv()
DASHSCOPE_API_KEY     = os.environ.get("DASHSCOPE_API_KEY")
DEEPSEEK_API_KEY      = os.environ.get("DEEPSEEK_API_KEY")
OSS_ACCESS_KEY_ID     = os.environ.get("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET")
OSS_BUCKET_NAME       = os.environ.get("OSS_BUCKET_NAME", "obsidian-remotely-1121")
OSS_ENDPOINT          = os.environ.get("OSS_ENDPOINT", "https://oss-cn-hongkong.aliyuncs.com")
DASHSCOPE_BASE_URL    = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")

missing = [k for k, v in {
    "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY,
    "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
    "OSS_ACCESS_KEY_ID": OSS_ACCESS_KEY_ID,
    "OSS_ACCESS_KEY_SECRET": OSS_ACCESS_KEY_SECRET,
}.items() if not v]
if missing:
    print(f"⚠️ 缺少环境变量: {', '.join(missing)}")

client = OpenAI(api_key=DEEPSEEK_API_KEY or "placeholder", base_url="https://api.deepseek.com")

_oss_bucket = None
def get_oss_bucket():
    global _oss_bucket
    if _oss_bucket is None:
        auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
        _oss_bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET_NAME)
    return _oss_bucket

app = FastAPI(title="Wiki API")

# ================= 2. OSS 工具函数 =================
def upload_audio_to_oss(audio_path: str):
    import re
    bucket = get_oss_bucket()
    
    # 清理文件名：只保留字母/数字/中文/常用符号
    basename = os.path.basename(audio_path)
    safe_basename = re.sub(r'[｜|<>:"/\\?*]', '_', basename)
    oss_key = f"temp_audio/{safe_basename}"
    
    bucket.put_object_from_file(oss_key, audio_path)
    signed_url = bucket.sign_url('GET', oss_key, 7200)
    print(f"✅ 音频已上传至 OSS: {oss_key}")
    return signed_url, oss_key

def save_markdown_to_oss(content: str, filename: str):
    get_oss_bucket().put_object(filename, content.encode("utf-8"))
    print(f"✅ Markdown 已保存至 OSS: {filename}")

def delete_oss_file(oss_key: str):
    try:
        get_oss_bucket().delete_object(oss_key)
        print(f"🗑️ OSS 临时文件已清理: {oss_key}")
    except:
        pass

def download_audio(url: str, output_dir: str = "/tmp/downloads"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    url = url.replace("m.bilibili.com", "www.bilibili.com")

    cookie_path = "/tmp/bilibili_cookies.txt"
    cookies_content = os.environ.get("BILIBILI_COOKIES", "")
    if cookies_content and not os.path.exists(cookie_path):
        with open(cookie_path, "w") as f:
            f.write(cookies_content)

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'outtmpl': f'{output_dir}/%(title)s_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'retries': 3,
        'socket_timeout': 60,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.bilibili.com/',
            },
    }
    
    # 如果有 cookie 文件，加上
    if os.path.exists(cookie_path):
        ydl_opts['cookiefile'] = cookie_path
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            mp3_path = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
            upload_date = info.get('upload_date', '')
            if upload_date and len(upload_date) == 8:
                publish_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            else:
                publish_date = datetime.now().strftime("%Y-%m-%d")
            duration = info.get('duration', 0) or 0
            # yt-dlp 有时返回 0，用 ffprobe 从文件本身读取时长兜底
            if not duration and os.path.exists(mp3_path):
                import subprocess, json as _json
                r = subprocess.run(
                    ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', mp3_path],
                    capture_output=True, text=True
                )
                if r.returncode == 0:
                    duration = float(_json.loads(r.stdout).get('format', {}).get('duration', 0))
            print(f"⏱️ 音频时长: {int(duration)//60} 分 {int(duration)%60} 秒")
            return mp3_path, info.get('title', 'Unknown'), publish_date, duration
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return None, None, datetime.now().strftime("%Y-%m-%d"), 0

# ================= 4. Fun-ASR 转写 =================
def transcribe_with_funasr(audio_path: str) -> str:
    audio_url, oss_key = upload_audio_to_oss(audio_path)
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": "fun-asr",
        "input": {"file_urls": [audio_url]},
        "parameters": {"diarization_enabled": True, "language_hints": ["zh", "en"]},
    }
    try:
        resp = requests.post(f"{DASHSCOPE_BASE_URL}/services/audio/asr/transcription",
                             headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        task_id = resp.json()["output"]["task_id"]
        print(f"✅ 转写任务已提交: {task_id}")
    except Exception as e:
        print(f"❌ 提交失败: {e}")
        delete_oss_file(oss_key)
        return ""

    query_headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    result = None
    for i in range(120):
        time.sleep(5)
        try:
            r = requests.get(f"{DASHSCOPE_BASE_URL}/tasks/{task_id}", headers=query_headers, timeout=15)
            r.raise_for_status()
            result = r.json()
            status = result["output"]["task_status"]
            if status == "SUCCEEDED":
                print("✅ 转写完成")
                break
            elif status == "FAILED":
                print(f"❌ 转写失败: {result}")
                delete_oss_file(oss_key)
                return ""
            elif i % 6 == 0:
                print(f"   状态: {status}...")
        except Exception as e:
            print(f"⚠️ 查询异常: {e}")
    else:
        print("❌ 转写超时")
        delete_oss_file(oss_key)
        return ""

    delete_oss_file(oss_key)

    try:
        sentences = []
        for item in result["output"]["results"]:
            if item.get("subtask_status") != "SUCCEEDED":
                continue
            trans_data = requests.get(item["transcription_url"], timeout=30).json()
            for transcript in trans_data.get("transcripts", []):
                for sent in transcript.get("sentences", []):
                    sentences.append({
                        "speaker": sent.get("speaker_id"),
                        "text": sent.get("text", "").strip()
                    })

        unique_speakers = {s["speaker"] for s in sentences if s["speaker"] is not None}
        multi_speaker = len(unique_speakers) > 1
        print(f"🎙️ 识别到 {len(unique_speakers)} 位说话人，{'区分 Speaker 标签' if multi_speaker else '单人，不加标签'}")

        full_text = ""
        for s in sentences:
            if not s["text"]:
                continue
            if multi_speaker and s["speaker"] is not None:
                full_text += f"**Speaker {s['speaker']}**: {s['text']}\n\n"
            else:
                full_text += f"{s['text']}\n\n"
        return full_text.strip()
    except Exception as e:
        print(f"❌ 解析结果失败: {e}")
        return ""

# ================= 5. 后台处理任务 =================
LONG_VIDEO_THRESHOLD = 900  # 15 分钟，单位秒

_PROMPT_BASE = """你是一个资深的知识库（Wiki）构建专家。请将以下待处理文本，整理为结构极其清晰的 Markdown 笔记。

【必须严格执行的输出结构】：

---
title: [根据内容生成一个准确且引人注目的标题]
date: {date}
tags:
  - [标签1，如：AI、投资、创业、健康]
  - [标签2]
  - [标签3，如有必要]
url: {url}
---
注意：tags 每个标签单独一行，直接写标签名，不加 # 前缀，根据内容提炼 2-4 个。

# 🎙️ [生成的标题]

**来源链接：** [点此访问]({url})

## 💡 核心脉络 (SCQA)
* **S (背景)**：讨论的初始场景是什么？
* **C (冲突)**：遇到了什么核心痛点或挑战？
* **Q (问题)**：引出的核心探讨问题是什么？

**A (解答与详实论证)**：
层层递进展开核心洞察，梳理论点、数据和细节。遇到精彩表达请用引用语块（>）保留原话。

{transcript_section}
以下是待处理文本：
{text}
"""

# 短内容（≤15分钟）：完整还原逐字稿
_TRANSCRIPT_SECTION = """---

## 📝 完整逐字稿
仅修复错别字、补全标点，删除"嗯""啊"等语气词，其余内容一字不差完整输出，不得跳段或省略任何一句话。对话形式请区分发言角色。

"""

# 长内容（>15分钟）：DeepSeek 节选精彩片段与金句
_HIGHLIGHTS_SECTION = """---

## ✨ 精彩片段与金句
从原文中节选最有洞见的对话片段和金句，每段保留原话并用引用块（>）呈现，可附一句话说明其价值或上下文。数量不限，宁多勿少，确保覆盖全文精华。

"""

def process_in_background(download_url: str, original_url: str, title: str, text: str):
    current_date = datetime.now().strftime("%Y-%m-%d")
    publish_date = current_date
    duration = 0
    print(f"🔄 后台开始处理: {download_url}")
    try:
        if text and len(text.strip()) > 10:
            raw_text = text
        else:
            mp3_path, detected_title, publish_date, duration = download_audio(download_url)
            if not mp3_path:
                print("❌ 下载失败，任务终止")
                return
            if detected_title:
                title = detected_title
            raw_text = transcribe_with_funasr(mp3_path)
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
            if not raw_text:
                print("❌ 转写失败，任务终止")
                return

        is_long = duration > LONG_VIDEO_THRESHOLD
        if is_long:
            print(f"⏱️ 长内容（{int(duration)//60} 分钟），DeepSeek 将节选精彩片段")
            transcript_section = _HIGHLIGHTS_SECTION
        else:
            print(f"⏱️ 短内容（{int(duration)//60} 分钟），完整还原逐字稿")
            transcript_section = _TRANSCRIPT_SECTION

        prompt = _PROMPT_BASE.format(
            date=publish_date, url=original_url,
            transcript_section=transcript_section, text=raw_text
        )

        print("📝 DeepSeek 润色中...")
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": "你是一个极其严谨的结构化知识提取助手。直接输出Markdown内容，第一行必须是 ---，禁止在frontmatter之前输出任何说明、问候或解释性文字。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=8192
        )
        final_markdown = response.choices[0].message.content

        # 裁掉 frontmatter 之前的任何废话
        if '---' in final_markdown:
            final_markdown = final_markdown[final_markdown.index('---'):]

        finish_reason = response.choices[0].finish_reason
        if finish_reason == 'length':
            print("⚠️ 输出被 max_tokens 截断")

        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
        filename = f"{safe_title}.md"
        save_markdown_to_oss(final_markdown, filename)
        print(f"✅ 全部完成！文件: {filename}")

    except Exception as e:
        print(f"❌ 后台处理异常: {e}")
        import traceback
        traceback.print_exc()

# ================= 6. API 接口（立即返回） =================
@app.post("/api/process")
def process_podcast_endpoint(req: dict):
    download_url = req.get("url", "")
    original_url = req.get("original_url") or download_url
    title = req.get("title", "未命名内容")
    text = req.get("text", "")
    raw = req.get("raw", False)

    if not download_url and not text:
        return {"status": "error", "message": "未提供 URL 或文本"}

    # raw 模式：跳过 DeepSeek，直接把文本包一层 frontmatter 存 OSS
    if raw and text:
        current_date = datetime.now().strftime("%Y-%m-%d")
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip() or "未命名内容"
        markdown = f"---\ntitle: {safe_title}\ndate: {current_date}\n---\n\n{text}"
        try:
            save_markdown_to_oss(markdown, f"{safe_title}.md")
            print(f"✅ 纯文本直接保存完成: {safe_title}.md")
            return {"status": "ok", "message": f"✅ 已保存：{safe_title}.md"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    print(f"🚀 收到请求: {download_url}，后台处理中...")
    thread = threading.Thread(
        target=process_in_background,
        args=(download_url, original_url, title, text),
        daemon=True
    )
    thread.start()

    return {
        "status": "accepted",
        "message": "✅ 已收到请求，正在后台处理。请在 2-5 分钟后同步 Obsidian 即可看到笔记。"
    }

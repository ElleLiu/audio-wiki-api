import os
import time
import threading
import requests
import yt_dlp
from datetime import datetime, timedelta
from fastapi import FastAPI, BackgroundTasks
from openai import OpenAI
from dotenv import load_dotenv

# ================= 1. 初始化配置 =================
load_dotenv()
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
DASHSCOPE_BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")

missing = [k for k, v in {"DASHSCOPE_API_KEY": DASHSCOPE_API_KEY, "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY, "GCS_BUCKET_NAME": GCS_BUCKET_NAME}.items() if not v]
if missing:
    print(f"⚠️ 缺少环境变量: {', '.join(missing)}")

client = OpenAI(api_key=DEEPSEEK_API_KEY or "placeholder", base_url="https://api.deepseek.com")

_gcs_bucket = None
def get_gcs_bucket():
    global _gcs_bucket
    if _gcs_bucket is None:
        from google.cloud import storage as gcs_storage
        _gcs_bucket = gcs_storage.Client().bucket(GCS_BUCKET_NAME)
    return _gcs_bucket

app = FastAPI(title="Wiki API")

# ================= 2. GCS 工具函数 =================
def upload_audio_to_gcs(audio_path: str):
    """上传音频，用 IAM 生成签名 URL"""
    import google.auth
    from google.auth.transport import requests as auth_requests

    bucket = get_gcs_bucket()
    blob_name = f"temp_audio/{os.path.basename(audio_path)}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(audio_path)

    credentials, _ = google.auth.default()
    credentials.refresh(auth_requests.Request())
    signed_url = blob.generate_signed_url(
        expiration=timedelta(hours=2),
        service_account_email=credentials.service_account_email,
        access_token=credentials.token,
    )
    print(f"✅ 音频已上传至 GCS: {blob_name}")
    return signed_url, blob_name

def save_markdown_to_gcs(content: str, filename: str):
    """把 Markdown 存到 GCS，供 Remotely Save 同步到 Obsidian"""
    bucket = get_gcs_bucket()
    # 存到根目录，与 Remotely Save 配置的同步路径一致
    blob = bucket.blob(filename)
    blob.upload_from_string(content.encode("utf-8"), content_type="text/markdown")
    print(f"✅ Markdown 已保存至 GCS: {filename}")

def delete_gcs_file(blob_name: str):
    try:
        get_gcs_bucket().blob(blob_name).delete()
        print(f"🗑️ GCS 临时文件已清理: {blob_name}")
    except:
        pass

# ================= 3. 音频下载 =================
def download_audio(url: str, output_dir: str = "/tmp/downloads"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'outtmpl': f'{output_dir}/%(title)s_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'retries': 3,
        'socket_timeout': 60,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            mp3_path = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
            return mp3_path, info.get('title', 'Unknown')
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return None, None

# ================= 4. Fun-ASR 转写 =================
def transcribe_with_funasr(audio_path: str) -> str:
    audio_url, blob_name = upload_audio_to_gcs(audio_path)

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
                delete_gcs_file(blob_name)
                return ""
        except Exception as e:
            print(f"⚠️ 查询异常: {e}")
    else:
        print("❌ 转写超时")
        delete_gcs_file(blob_name)
        return ""

    delete_gcs_file(blob_name)

    try:
        full_text = ""
        for item in result["output"]["results"]:
            if item.get("subtask_status") != "SUCCEEDED":
                continue
            trans_data = requests.get(item["transcription_url"], timeout=30).json()
            for transcript in trans_data.get("transcripts", []):
                for sent in transcript.get("sentences", []):
                    speaker = sent.get("speaker_id")
                    text = sent.get("text", "").strip()
                    full_text += f"**Speaker {speaker}**: {text}\n\n" if speaker is not None else f"{text}\n\n"
        return full_text.strip()
    except Exception as e:
        print(f"❌ 解析结果失败: {e}")
        return ""

# ================= 5. DeepSeek 润色 =================
PROMPT_TEMPLATE = """你是一个资深的知识库（Wiki）构建专家。请将以下待处理文本，整理为结构极其清晰的 Markdown 笔记。

【必须严格执行的输出结构】：

---
title: [根据内容生成一个准确且引人注目的标题]
date: {date}
tags: 
  - #内容提取
  - #[提取2-3个核心领域的标签]
url: {url}
---

# 🎙️ [生成的标题]

**来源链接：** [点此访问]({url})

## 💡 核心脉络 (SCQA)
* **S (背景)**：讨论的初始场景是什么？
* **C (冲突)**：遇到了什么核心痛点或挑战？
* **Q (问题)**：引出的核心探讨问题是什么？

**A (解答与详实论证)**：
层层递进地展开核心洞察与结论，梳理论点、数据和细节。遇到精彩表达请用引用语块（>）保留原话。

---

## 📝 深度精炼逐字稿 (原文回放)
修复错别字，加标点，精简废话但完整保留干货。对话形式请区分发言角色。

以下是待处理文本：
{text}
"""

def refine_with_deepseek(raw_text: str, original_url: str, current_date: str) -> str:
    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": "你是一个极其严谨的结构化知识提取助手。"},
            {"role": "user", "content": PROMPT_TEMPLATE.format(date=current_date, url=original_url, text=raw_text)}
        ],
        temperature=0.3
    )
    return response.choices[0].message.content

# ================= 6. 后台处理任务 =================
def process_in_background(download_url: str, original_url: str, title: str, text: str):
    """完整处理流程，在后台线程运行"""
    current_date = datetime.now().strftime("%Y-%m-%d")
    print(f"🔄 后台开始处理: {download_url}")

    try:
        if text and len(text.strip()) > 10:
            raw_text = text
            print("💡 使用直接文本")
        else:
            mp3_path, detected_title = download_audio(download_url)
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

        print("📝 DeepSeek 润色中...")
        final_markdown = refine_with_deepseek(raw_text, original_url, current_date)

        # 存到 GCS，Remotely Save 会自动同步到 Obsidian
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
        filename = f"{safe_title}_{current_date}.md"
        save_markdown_to_gcs(final_markdown, filename)
        print(f"✅ 全部完成！文件已存至 GCS: {filename}")

    except Exception as e:
        print(f"❌ 后台处理异常: {e}")
        import traceback
        traceback.print_exc()

# ================= 7. API 接口（立即返回） =================
@app.post("/api/process")
def process_podcast_endpoint(req: dict):
    download_url = req.get("url", "")
    original_url = req.get("original_url") or download_url
    title = req.get("title", "未命名内容")
    text = req.get("text", "")

    if not download_url and not text:
        return {"status": "error", "message": "未提供 URL 或文本"}

    print(f"🚀 收到请求: {download_url}，立即返回，后台处理中...")

    # 用线程在后台处理，不阻塞响应
    thread = threading.Thread(
        target=process_in_background,
        args=(download_url, original_url, title, text),
        daemon=True
    )
    thread.start()

    # 立刻返回，快捷指令不会超时
    return {
        "status": "accepted",
        "message": "✅ 已收到请求，正在后台处理。请在 2-5 分钟后打开 Obsidian 同步即可看到笔记。"
    }

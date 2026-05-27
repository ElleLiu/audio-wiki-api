import os
import time
import requests
import yt_dlp
from datetime import datetime
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from dotenv import load_dotenv

# ================= 1. 初始化配置 =================
load_dotenv()
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")  # 百炼 API Key
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")      # 你现有的 GCS 存储桶

# 不再在启动时崩溃，改为警告（让容器至少能启动）
missing = [k for k, v in {"DASHSCOPE_API_KEY": DASHSCOPE_API_KEY, "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY, "GCS_BUCKET_NAME": GCS_BUCKET_NAME}.items() if not v]
if missing:
    print(f"⚠️ 缺少环境变量: {', '.join(missing)}，相关功能将不可用")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY or "placeholder",
    base_url="https://api.deepseek.com"
)

# GCS 延迟初始化（避免启动时崩溃）
_gcs_bucket = None
def get_gcs_bucket():
    global _gcs_bucket
    if _gcs_bucket is None:
        from google.cloud import storage as gcs_storage
        gcs_client = gcs_storage.Client()
        _gcs_bucket = gcs_client.bucket(GCS_BUCKET_NAME)
    return _gcs_bucket

# 百炼 API 端点（北京，享受便宜价格 + 免费额度）
# 如果从东京调用不稳定，可换成国际版: https://dashscope-intl.aliyuncs.com/api/v1
DASHSCOPE_BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")

# 初始化 FastAPI 引擎
app = FastAPI(title="Wiki API", description="播客/网页双模自动转 Wiki 云端服务")

# ================= 2. 内部音频处理函数 =================
def download_audio(url: str, output_dir: str = "/tmp/downloads"):
    """下载音频，返回 (文件路径, 视频标题)"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'outtmpl': f'{output_dir}/%(title)s_%(id)s.%(ext)s', 
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            mp3_path = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
            title = info.get('title', 'Unknown_Podcast')
            return mp3_path, title
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return None, None

def upload_audio_to_gcs(audio_path: str):
    """上传音频到 GCS 并返回公网 URL"""
    bucket = get_gcs_bucket()
    blob_name = f"temp_audio/{os.path.basename(audio_path)}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(audio_path)
    # 生成 2 小时有效的签名 URL
    signed_url = blob.generate_signed_url(expiration=7200)
    print(f"✅ 音频已上传至 GCS: {blob_name}")
    return signed_url, blob_name

def transcribe_with_funasr(audio_path: str) -> str:
    """调用百炼 Fun-ASR 录音文件识别（替换 Deepgram）"""
    
    # 第一步：上传音频到 GCS，获取公网 URL
    audio_url, blob_name = upload_audio_to_gcs(audio_path)
    
    # 第二步：提交异步转写任务
    submit_url = f"{DASHSCOPE_BASE_URL}/services/audio/asr/transcription"
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": "fun-asr",
        "input": {"file_urls": [audio_url]},
        "parameters": {
            "diarization_enabled": True,
            "language_hints": ["zh", "en"],
        }
    }
    
    try:
        resp = requests.post(submit_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        task_id = resp.json()["output"]["task_id"]
        print(f"✅ 转写任务已提交，task_id: {task_id}")
    except Exception as e:
        print(f"❌ 提交转写任务失败: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"详情: {e.response.text}")
        return ""

    # 第三步：轮询等待结果
    query_url = f"{DASHSCOPE_BASE_URL}/tasks/{task_id}"
    query_headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    
    print("⏳ 等待 Fun-ASR 转写完成...")
    result = None
    for i in range(120):  # 最多等 10 分钟
        time.sleep(5)
        try:
            status_resp = requests.get(query_url, headers=query_headers, timeout=15)
            status_resp.raise_for_status()
            result = status_resp.json()
            task_status = result["output"]["task_status"]
            
            if task_status == "SUCCEEDED":
                print("✅ Fun-ASR 转写完成！")
                break
            elif task_status == "FAILED":
                print(f"❌ 转写失败: {result}")
                return ""
            elif i % 6 == 0:
                print(f"   状态: {task_status}...")
        except Exception as e:
            print(f"⚠️ 查询状态异常: {e}")
    else:
        print("❌ 转写超时")
        return ""
    
    # 第四步：解析转写结果
    try:
        full_text = ""
        for item in result["output"]["results"]:
            if item.get("subtask_status") != "SUCCEEDED":
                continue
            trans_url = item.get("transcription_url")
            if not trans_url:
                continue
            trans_resp = requests.get(trans_url, timeout=30)
            trans_data = trans_resp.json()
            
            for transcript in trans_data.get("transcripts", []):
                for sent in transcript.get("sentences", []):
                    speaker_id = sent.get("speaker_id")
                    text = sent.get("text", "").strip()
                    if speaker_id is not None:
                        full_text += f"**Speaker {speaker_id}**: {text}\n\n"
                    else:
                        full_text += f"{text}\n\n"
        
        # 清理 GCS 临时文件
        try:
            get_gcs_bucket().blob(blob_name).delete()
            print("🗑️ GCS 临时音频已清理")
        except:
            pass
        
        return full_text.strip()
    except Exception as e:
        print(f"❌ 解析转写结果失败: {e}")
        return ""

# ================= 3. 对外开放的 API 接口 =================
@app.post("/api/process")
def process_podcast_endpoint(req: dict):
    download_url = req.get("url", "")
    original_url = req.get("original_url") or download_url
    title = req.get("title", "未命名内容")
    text = req.get("text", "")
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n🚀 收到手机端请求，开始处理下载链接: {download_url}")
    
    if text and len(text.strip()) > 10:
        print("💡 侦测到前端探针传入的直接文本，跳过音视频下载与转录流程！")
        raw_text = text
    else:
        print("🎵 未检测到直接文本，启动播客下载转录流水线...")
        if not download_url:
            raise HTTPException(status_code=400, detail="未提供有效的 URL")
            
        audio_info = download_audio(download_url)
        if not audio_info[0]:
            raise HTTPException(status_code=400, detail="音频下载失败，可能是反爬或链接无效。")
        mp3_path, title = audio_info
        
        print("🧠 Fun-ASR 转录中...")
        raw_text = transcribe_with_funasr(mp3_path)
        
        if os.path.exists(mp3_path):
            os.remove(mp3_path)
            
        if not raw_text:
            raise HTTPException(status_code=500, detail="Fun-ASR 语音转录失败。")
            
    print("DeepSeek 认知重构中...")
    
    PROMPT_TEMPLATE = """你是一个资深的知识库（Wiki）构建专家。请将以下待处理文本，整理为结构极其清晰的 Markdown 笔记。

【必须严格执行的输出结构】：

---
title: [根据内容生成一个准确且引人注目的标题]
date: {date}
tags: 
  - #内容提取
  - #[提取2-3个核心领域的标签，例如 #AI应用, #宏观经济 等]
url: {url}
---

# 🎙️ [生成的标题]

**来源链接：** [点此访问]({url})

## 💡 核心脉络 (SCQA)
请用精炼的语言，提取这段内容的逻辑主线：
* **S (背景)**：讨论的初始场景、行业现状或普遍共识是什么？
* **C (冲突)**：遇到了什么核心痛点、挑战、变量或反常现象？
* **Q (问题)**：基于上述冲突，引出的核心探讨问题是什么？

**A (解答与详实论证)**：
这是本篇笔记的核心。请针对上述问题，给出逻辑清晰、证据详实的论述。
要求：
1. 层层递进地展开讲者或文章给出的核心洞察与结论。
2. 梳理支撑结论的具体论点、数据案例和重要细节。
3. 采用清晰的层级列表（带逻辑递进），务必贴近原意。遇到极其精彩的表达，请保留"原话"并用引用语块（>）标识。

---

## 📝 深度精炼逐字稿 (原文回放)
为了方便未来回顾上下文，请对原始文本进行"神级还原"与润色：
1. 修复所有由于语音转写或网页抓取导致的错别字。
2. 加上正确的标点符号，做好清晰的段落分割。
3. 将过于口语化的废话适当精简，但【必须完整保留】干货、核心逻辑以及原始的语气。
4. 如果明显是对话形式，请尽量区分逻辑段落或发言角色。

以下是待处理文本：
{text}
"""
    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": "你是一个极其严谨的结构化知识提取助手。"},
                {"role": "user", "content": PROMPT_TEMPLATE.format(date=current_date, url=original_url, text=raw_text)}
            ],
            temperature=0.3
        )
        final_markdown = response.choices[0].message.content
    except Exception as e:
        print(f"❌ 大模型调用失败: {e}")
        raise HTTPException(status_code=500, detail=f"大模型提炼失败: {e}")
        
    print("✅ 处理完毕，准备将数据传回手机！")
    return {
        "status": "success",
        "title": title,
        "markdown": final_markdown
    }

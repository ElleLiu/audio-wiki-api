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

import os
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv

# ================= 1. 初始化配置 =================
load_dotenv()
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

if not DEEPGRAM_API_KEY or not DEEPSEEK_API_KEY:
    raise RuntimeError("⚠️ 请在 .env 文件中配置好 API Key！")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 初始化 FastAPI 引擎
app = FastAPI(title="Wiki API", description="播客自动转 Wiki 云端服务")

# 定义手机端发过来的数据格式
class PodcastRequest(BaseModel):
    url: str

# ================= 2. 核心 Prompt (新增了 YAML 元数据要求) =================
SYSTEM_PROMPT = """
你是一个资深的播客编辑和个人知识库（Wiki）构建专家。
请对收到的粗糙转录文本进行“神级还原”与结构化提炼。

【必须严格执行的输出结构】：

---
title: [根据内容生成一个引人注目的标题]
date: [当前日期，格式 YYYY-MM-DD]
tags: 
  - #播客
  - #[提取2-3个核心领域的标签，例如 #AI应用, #稳定币]
---

# 🎙️ [播客标题]

## 📌 核心知识点 (Key Takeaways)
- 提取 3-5 个最具洞察力的核心观点（用一句话总结，带粗体强调）。

## 📝 深度精炼逐字稿
（区分 主持人/嘉宾，修复所有错别字，加上正确的标点符号。将口语化的废话适当精简，保留干货和核心逻辑。）
"""

# ================= 3. 内部处理函数 =================
def download_audio(url: str, output_dir: str = "downloads"):
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

def transcribe_with_deepgram(audio_path: str) -> str:
    """调用 Deepgram 转录"""
    url = "https://api.deepgram.com/v1/listen"
    params = {"model": "nova-2", "punctuate": "true", "diarize": "true", "language": "zh"}
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "audio/mp3"}
    try:
        with open(audio_path, "rb") as audio_file:
            response = requests.post(url, params=params, headers=headers, data=audio_file, timeout=600)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("results", {})
        utterances = results.get("utterances", [])
        if utterances:
            return "".join([f"**Speaker {u.get('speaker', 0)}**: {u.get('transcript', '').strip()}\n\n" for u in utterances])
            
        channels = results.get("channels", [])
        if channels and channels[0].get("alternatives"):
            return channels[0]["alternatives"][0].get("transcript", "").strip()
        return ""
    except Exception:
        return ""

def refine_text_with_llm(raw_text: str) -> str:
    """调用 DeepSeek 提炼"""
    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash", 
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"以下是原始转录文本，请开始你的工作：\n\n{raw_text}"}
            ],
            temperature=0.3, 
        )
        return response.choices[0].message.content
    except Exception:
        return ""

# ================= 4. 对外开放的 API 接口 =================
@app.post("/api/process")
def process_podcast_endpoint(req: PodcastRequest):
    print(f"\n🚀 收到手机端请求，开始处理 URL: {req.url}")
    
    # 1. 下载
    print("下载音频中...")
    audio_info = download_audio(req.url)
    if not audio_info[0]:
        raise HTTPException(status_code=400, detail="音频下载失败，可能是反爬或链接无效。")
    mp3_path, title = audio_info
    
    # 2. 转录
    print("Deepgram 转录中...")
    raw_text = transcribe_with_deepgram(mp3_path)
    
    # 【极客细节】：云端硬盘很小，处理完必须随手删除原 mp3 文件释放空间
    if os.path.exists(mp3_path):
        os.remove(mp3_path)
        
    if not raw_text:
        raise HTTPException(status_code=500, detail="Deepgram 语音转录失败。")
        
    # 3. 提炼
    print("DeepSeek 认知重构中...")
    final_markdown = refine_text_with_llm(raw_text)
    if not final_markdown:
        raise HTTPException(status_code=500, detail="大模型提炼失败。")
        
    print("✅ 处理完毕，准备将数据传回手机！")
    # 直接将文本结果作为 API 响应发回给手机
    return {
        "status": "success",
        "title": title,
        "markdown": final_markdown
    } 

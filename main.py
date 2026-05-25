import os
import requests
import yt_dlp
from datetime import datetime
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from dotenv import load_dotenv

# ================= 1. 初始化配置 =================
load_dotenv()
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

if not DEEPGRAM_API_KEY or not DEEPSEEK_API_KEY:
    raise RuntimeError("⚠️ 请在环境变量中配置好 API Key！")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 初始化 FastAPI 引擎
app = FastAPI(title="Wiki API", description="播客/网页双模自动转 Wiki 云端服务")

# ================= 2. 内部音频处理函数 =================
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
    except Exception as e:
        print(f"❌ Deepgram 转录异常: {e}")
        return ""

# ================= 3. 对外开放的 API 接口 =================
@app.post("/api/process")
def process_podcast_endpoint(req: dict):
    download_url = req.get("url", "")
    # 新增：接收用于在 Markdown 里展示的原始链接
    original_url = req.get("original_url") or download_url 
    title = req.get("title", "未命名内容")
    text = req.get("text", "")
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n🚀 收到手机端请求，开始处理下载链接: {download_url}")
    
    # 交通枢纽分流逻辑
    if text and len(text.strip()) > 10:
        print("💡 侦测到前端探针传入的直接文本，跳过音视频下载与转录流程！")
        raw_text = text
    else:
        print("🎵 未检测到直接文本，启动播客下载转录流水线...")
        if not url:
            raise HTTPException(status_code=400, detail="未提供有效的 URL")
            
        audio_info = download_audio(url)
        if not audio_info[0]:
            raise HTTPException(status_code=400, detail="音频下载失败，可能是反爬或链接无效。")
        mp3_path, title = audio_info
        
        print("Deepgram 转录中...")
        raw_text = transcribe_with_deepgram(mp3_path)
        
        if os.path.exists(mp3_path):
            os.remove(mp3_path)
            
        if not raw_text:
            raise HTTPException(status_code=500, detail="Deepgram 语音转录失败。")
            
    # 重构提炼逻辑：YAML + SCQA (强化A) + 原文
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
3. 采用清晰的层级列表（带逻辑递进），务必贴近原意。遇到极其精彩的表达，请保留“原话”并用引用语块（>）标识。

---

## 📝 深度精炼逐字稿 (原文回放)
为了方便未来回顾上下文，请对原始文本进行“神级还原”与润色：
1. 修复所有由于语音转写或网页抓取导致的错别字。
2. 加上正确的标点符号，做好清晰的段落分割。
3. 将过于口语化的废话适当精简，但【必须完整保留】干货、核心逻辑以及原始的语气。
4. 如果明显是对话形式，请尽量区分逻辑段落或发言角色。

以下是待处理文本：
{text}
"""
    try:
        # 回调模型版本，并注入 date, url, text 变量
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

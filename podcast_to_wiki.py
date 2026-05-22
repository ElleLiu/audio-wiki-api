import os
import sys
import argparse
import requests
import yt_dlp
from dotenv import load_dotenv

# ================= 1. 初始化与配置校验 =================
load_dotenv()
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")

if not DEEPGRAM_API_KEY or DEEPGRAM_API_KEY.startswith("换成你实际"):
    print("⚠️  错误: 找不到有效的 Deepgram API Key！")
    sys.exit(1)

# ================= 2. 音频下载模块 (yt-dlp) =================
def download_audio(url: str, output_dir: str = "downloads") -> str:
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    print(f"\n[步骤 1/3] 🚀 开始解析并提取音频: {url}")
    
    # 获取当前脚本所在的绝对路径，告诉 yt-dlp 去哪里找 ffmpeg.exe
    current_dir = os.path.dirname(os.path.abspath(__file__))

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': f'{output_dir}/%(title)s_%(id)s.%(ext)s', 
        'quiet': False,
        'no_warnings': True,
        # 【核心修复】强制指定 ffmpeg 路径为当前文件夹
        # 'ffmpeg_location': current_dir, 
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
            print(f"✅ 音频下载成功: {downloaded_file}")
            return downloaded_file
    except Exception as e:
        print(f"❌ 音频下载失败: {e}")
        return ""

# ================= 3. 音频转录模块 (Deepgram API) =================
def transcribe_with_deepgram(audio_path: str) -> str:
    print(f"\n[步骤 2/3] 🧠 正在上传至 Deepgram 远程服务器转录...")
    url = "https://api.deepgram.com/v1/listen"
    
    params = {
        "model": "nova-2",       
        "punctuate": "true",  # 换成这个，强制加标点
        "diarize": "true",       
        "language": "zh"         
    }
    headers = {
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "audio/mp3"
    }

    try:
        with open(audio_path, "rb") as audio_file:
            response = requests.post(url, params=params, headers=headers, data=audio_file, timeout=600)
            
        response.raise_for_status()
        data = response.json()
        
        # 【核心修复：多重容错解析逻辑】
        results = data.get("results", {})
        
        # 1. 首选方案：尝试获取带说话人分离的对话 (utterances)
        utterances = results.get("utterances", [])
        if utterances:
            formatted_transcript = ""
            for utterance in utterances:
                speaker_id = utterance.get("speaker", 0)
                text = utterance.get("transcript", "").strip()
                formatted_transcript += f"**Speaker {speaker_id}**: {text}\n\n"
            print("✅ Deepgram 转录圆满完成！(已成功区分说话人)")
            return formatted_transcript
            
        # 2. 备用方案：如果没有 utterances，直接抓取完整合并文本
        channels = results.get("channels", [])
        if channels and channels[0].get("alternatives"):
            basic_text = channels[0]["alternatives"][0].get("transcript", "").strip()
            if basic_text:
                print("✅ Deepgram 转录完成！(注：模型判定为单人说话或分离失败，返回合并纯文本)")
                return basic_text
                
        # 3. 终极兜底：如果还是啥也没有，把 Deepgram 的底裤（原始 JSON）打印出来排查
        print("⚠️ Deepgram 返回了成功状态，但在常规字段中找不到文本。原始返回数据如下：")
        print(data)
        return ""

    except Exception as e:
        print(f"❌ Deepgram 转录失败: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"服务器报错详情: {e.response.text}")
        return ""

# ================= 4. Wiki 入库模块 =================
def save_to_wiki(text: str, filename: str = "podcast_draft_notes.md"):
    print(f"\n[步骤 3/3] 💾 正在写入本地知识库...")
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# 播客原始转录纪要\n\n> 来源: 自动化流水线抓取\n\n---\n\n{text}")
        print(f"🎉 恭喜！文件已生成: {os.path.abspath(filename)}")
    except Exception as e:
        print(f"❌ 保存文件失败: {e}")

# ================= 5. 主程序入口 (命令行解析) =================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全自动播客转录 Wiki 流水线")
    parser.add_argument("-url", type=str, required=True, help="需要抓取的视频或音频链接")
    args = parser.parse_args()
    
    target_url = args.url 
    
    downloaded_mp3 = download_audio(target_url)
    
    if downloaded_mp3:
        transcript_result = transcribe_with_deepgram(downloaded_mp3)
        if transcript_result:
            # 【核心修改区：智能命名】
            # 1. os.path.basename 提取纯文件名 (例如: 播客标题_id.mp3)
            base_name = os.path.basename(downloaded_mp3)
            # 2. os.path.splitext 把文件名和后缀分开，[0] 取前半部分
            file_name_without_ext = os.path.splitext(base_name)[0]
            # 3. 拼接成最终的 md 文件名
            dynamic_md_name = f"{file_name_without_ext}.md"
            
            save_to_wiki(transcript_result, dynamic_md_name)
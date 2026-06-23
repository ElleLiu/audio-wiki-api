import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


def probe_duration(media_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(media_path),
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        return float(json.loads(result.stdout).get("format", {}).get("duration", 0) or 0)
    except Exception as exc:
        print(f"⚠️ 读取媒体时长失败，将按短内容处理: {exc}")
        return 0


def extract_audio(media_path: Path, output_dir: Path) -> Path:
    safe_stem = "".join(c for c in media_path.stem if c.isalnum() or c in (" ", "-", "_")).strip() or "local_video"
    audio_path = output_dir / f"{safe_stem}.mp3"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    print(f"🎬 正在抽取音频: {media_path}")
    subprocess.run(cmd, check=True)
    print(f"✅ 音频已生成: {audio_path}")
    return audio_path


def default_publish_date(media_path: Path) -> str:
    return datetime.fromtimestamp(media_path.stat().st_mtime).strftime("%Y-%m-%d")


def process_local_media(media_path: Path, title: Optional[str] = None, source_url: Optional[str] = None, publish_date: Optional[str] = None) -> str:
    try:
        from main import generate_and_save_markdown, transcribe_with_funasr
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"缺少 Python 依赖: {exc.name}。请先执行 pip install -r requirements.txt") from exc

    media_path = media_path.expanduser().resolve()
    if not media_path.exists():
        raise FileNotFoundError(f"文件不存在: {media_path}")
    if not media_path.is_file():
        raise ValueError(f"不是文件: {media_path}")

    note_title = title or media_path.stem
    note_source = source_url or media_path.as_uri()
    note_date = publish_date or default_publish_date(media_path)
    duration = probe_duration(media_path)
    print(f"⏱️ 媒体时长: {int(duration)//60} 分 {int(duration)%60} 秒")

    with tempfile.TemporaryDirectory(prefix="audio-wiki-local-") as tmpdir:
        audio_path = extract_audio(media_path, Path(tmpdir))
        raw_text = transcribe_with_funasr(str(audio_path))
        if not raw_text:
            raise RuntimeError("转写失败，未生成笔记")
        return generate_and_save_markdown(
            raw_text=raw_text,
            title=note_title,
            source_url=note_source,
            publish_date=note_date,
            duration=duration,
            is_webpage=False,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 PC 本地视频/音频转成 Obsidian Markdown 笔记并保存到 OSS。")
    parser.add_argument("media_file", help="本地视频或音频文件路径，例如 /Users/me/Desktop/video.mp4")
    parser.add_argument("--title", help="笔记标题，默认使用文件名")
    parser.add_argument("--source-url", help="写入 frontmatter/url 的来源链接，默认使用本地 file:// 路径")
    parser.add_argument("--date", help="写入 frontmatter/date 的日期，默认使用文件修改日期，格式 YYYY-MM-DD")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        filename = process_local_media(
            Path(args.media_file),
            title=args.title,
            source_url=args.source_url,
            publish_date=args.date,
        )
        print(f"🎉 已保存到 OSS，Obsidian 同步后可见: {filename}")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"❌ FFmpeg/FFprobe 执行失败: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"❌ 本地媒体处理失败: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

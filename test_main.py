import os
import io
import sys
import types
import unittest
from unittest.mock import patch

# URL routing tests do not need the web server or cloud clients. Keep them
# runnable in a minimal local Python environment.
try:
    import oss2  # noqa: F401
except ImportError:
    sys.modules["oss2"] = types.SimpleNamespace()

try:
    import fastapi  # noqa: F401
except ImportError:
    class _FastAPI:
        def __init__(self, **_kwargs):
            pass

        def post(self, _path):
            return lambda function: function

    sys.modules["fastapi"] = types.SimpleNamespace(FastAPI=_FastAPI)

try:
    import openai  # noqa: F401
except ImportError:
    class _OpenAI:
        def __init__(self, **_kwargs):
            pass

    sys.modules["openai"] = types.SimpleNamespace(OpenAI=_OpenAI)

try:
    import dotenv  # noqa: F401
except ImportError:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda: None)

import main


class UrlExtractionTests(unittest.TestCase):
    def test_extracts_douyin_url_from_share_text(self):
        share_text = (
            "8.48 复制打开抖音，看看【九姐姐的作品】有钱人给女儿的财富笔记 "
            "https://v.douyin.com/8oazqCWCq3U/ G@i.ca qeb:/ 05/08 :9pm"
        )

        self.assertEqual(
            main.extract_first_url(share_text),
            "https://v.douyin.com/8oazqCWCq3U/",
        )

    def test_strips_chinese_trailing_punctuation(self):
        self.assertEqual(
            main.extract_first_url("打开 https://v.douyin.com/example/。"),
            "https://v.douyin.com/example/",
        )

    def test_endpoint_passes_extracted_url_to_worker(self):
        share_text = "复制打开抖音 https://v.douyin.com/8oazqCWCq3U/ 立即观看"

        with patch.object(main.threading, "Thread") as thread:
            response = main.process_podcast_endpoint({"url": share_text})

        self.assertEqual(response["status"], "accepted")
        _, kwargs = thread.call_args
        self.assertEqual(
            kwargs["args"][:2],
            (
                "https://v.douyin.com/8oazqCWCq3U/",
                "https://v.douyin.com/8oazqCWCq3U/",
            ),
        )

    def test_douyin_uses_its_own_cookie_and_referer(self):
        with patch.object(
            main,
            "_write_cookie_file",
            return_value="/tmp/douyin_cookies.txt",
        ) as write_cookie:
            headers, cookie_path = main._download_site_options(
                "https://v.douyin.com/8oazqCWCq3U/"
            )

        self.assertEqual(headers["Referer"], "https://www.douyin.com/")
        self.assertEqual(cookie_path, "/tmp/douyin_cookies.txt")
        write_cookie.assert_called_once_with(
            "DOUYIN_COOKIES", "/tmp/douyin_cookies.txt"
        )

    def test_site_without_cookie_configuration_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            headers, cookie_path = main._download_site_options(
                "https://example.com/video"
            )

        self.assertNotIn("Referer", headers)
        self.assertIsNone(cookie_path)

    def test_rednote_uses_its_own_cookie_and_referer(self):
        with patch.object(
            main,
            "_write_cookie_file",
            return_value="/tmp/rednote_cookies.txt",
        ) as write_cookie:
            headers, cookie_path = main._download_site_options(
                "https://xhslink.cn/o/example"
            )

        self.assertEqual(headers["Referer"], "https://www.xiaohongshu.com/")
        self.assertEqual(cookie_path, "/tmp/rednote_cookies.txt")
        write_cookie.assert_called_once_with(
            "REDNOTE_COOKIES", "/tmp/rednote_cookies.txt"
        )

    def test_builds_canonical_rednote_url_from_shortlink_redirect(self):
        redirected = (
            "https://www.xiaohongshu.com/explore?target_note_id=abc123"
            "&xsec_token=token-value&xsec_source=pc_share"
        )

        canonical = main._canonical_rednote_url(redirected)

        self.assertEqual(
            canonical,
            "https://www.xiaohongshu.com/explore/abc123?target_note_id=abc123"
            "&xsec_token=token-value&xsec_source=pc_share",
        )

    def test_extracts_rednote_images_from_initial_state(self):
        html = '''
        <script>
        window.__INITIAL_STATE__ = {
          "note": {"noteDetailMap": {"abc123": {"note": {
            "title": "图文标题",
            "desc": "图文正文",
            "time": 1786636800000,
            "imageList": [
              {"urlDefault": "https://img.example/1.jpg"},
              {"urlDefault": "https://img.example/2.jpg"}
            ]
          }}}}
        };
        </script>
        '''

        note = main._extract_rednote_note(
            html,
            "https://www.xiaohongshu.com/explore",
        )

        self.assertEqual(note["id"], "abc123")
        self.assertEqual(note["title"], "图文标题")
        self.assertEqual(note["description"], "图文正文")
        self.assertEqual(
            note["image_urls"],
            ["https://img.example/1.jpg", "https://img.example/2.jpg"],
        )

    def test_appends_obsidian_image_gallery(self):
        markdown = main._append_image_gallery(
            "---\ntitle: 测试\n---\n\n正文",
            ["assets/rednote/a.webp", "assets/rednote/b.webp"],
        )

        self.assertIn("## 原图", markdown)
        self.assertIn("![小红书图片 1](assets/rednote/a.webp)", markdown)
        self.assertIn("![小红书图片 2](assets/rednote/b.webp)", markdown)

    def test_appends_ocr_text_under_each_image(self):
        markdown = main._append_image_gallery(
            "正文", ["assets/rednote/a.webp", "assets/rednote/b.webp"],
            ["第一张文字\n第二行", "第二张文字"],
        )
        self.assertIn("## 图片文字（OCR）\n\n### 图片 1\n\n第一张文字\n第二行", markdown)
        self.assertIn("### 图片 2\n\n第二张文字", markdown)

    def test_image_ocr_failure_keeps_image_and_marks_failure(self):
        note = {"title": "测试", "description": "正文", "image_urls": ["https://img.example/1.jpg"]}
        with (
            patch.object(main, "_compress_and_upload_rednote_image", return_value=("assets/rednote/a.webp", b"webp")),
            patch.object(main, "_rednote_image_fingerprint", return_value=1),
            patch.object(main, "get_oss_bucket"),
            patch.object(main, "_ocr_rednote_image", side_effect=RuntimeError("unavailable")),
        ):
            result = main._save_rednote_images(note, "https://www.xiaohongshu.com/")
        self.assertEqual(result["image_paths"], ["assets/rednote/a.webp"])
        self.assertEqual(result["image_ocr"], ["（OCR 识别失败，请查看原图）"])

    def test_ocr_sends_webp_bytes_and_preserves_transcription(self):
        response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"output": {"choices": [{"message": {"content": [{"text": "图中文字\n第二行"}]}}]}},
        )
        with patch.object(main.requests, "post", return_value=response) as post:
            text = main._ocr_rednote_image(b"webp")
        self.assertEqual(text, "图中文字\n第二行")
        self.assertEqual(post.call_args.kwargs["json"]["parameters"]["ocr_options"]["task"], "text_recognition")

    def test_rejects_coordinate_only_ocr_output(self):
        response = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"output": {"choices": [{"message": {"content": [{"text": "374,292,91,511,90\n454,428,95,663,90"}]}}]}},
        )
        with patch.object(main.requests, "post", return_value=response):
            with self.assertRaisesRegex(ValueError, "coordinates"):
                main._ocr_rednote_image(b"webp")

    def test_deduplicates_similar_images_before_upload_and_ocr(self):
        note = {"title": "测试", "description": "正文", "image_urls": ["one", "two", "three"]}
        images = [("assets/rednote/a.webp", b"first"),
                  ("assets/rednote/b.webp", b"variant"),
                  ("assets/rednote/c.webp", b"different")]
        with (
            patch.object(main, "_compress_and_upload_rednote_image", side_effect=images),
            patch.object(main, "_rednote_image_fingerprint", side_effect=[0, 1, 0xffffffffffffffff]),
            patch.object(main, "get_oss_bucket") as bucket,
            patch.object(main, "_ocr_rednote_image", return_value="文字") as ocr,
        ):
            result = main._save_rednote_images(note, "https://www.xiaohongshu.com/")
        self.assertEqual(result["image_paths"], [images[0][0], images[2][0]])
        self.assertEqual(bucket.return_value.put_object.call_count, 2)
        self.assertEqual(ocr.call_count, 2)

    def test_deduplicates_ytdlp_cover_variants_with_identical_ocr(self):
        note = {"title": "测试", "description": "正文", "image_urls": ["cover-small", "cover-large"],
                "images_from_thumbnails": True}
        with (
            patch.object(main, "_compress_and_upload_rednote_image", side_effect=[
                ("assets/rednote/a.webp", b"small"), ("assets/rednote/b.webp", b"large")]),
            patch.object(main, "_rednote_image_fingerprint", side_effect=[0, 0xffffffffffffffff]),
            patch.object(main, "_ocr_rednote_image", return_value="39岁被裁一个月复盘说几句实话"),
            patch.object(main, "get_oss_bucket") as bucket,
        ):
            result = main._save_rednote_images(note, "https://www.xiaohongshu.com/")
        self.assertEqual(result["image_paths"], ["assets/rednote/a.webp"])
        self.assertEqual(result["image_ocr"], ["39岁被裁一个月复盘说几句实话"])
        bucket.return_value.put_object.assert_called_once()

    def test_saves_rednote_original_text_and_images_without_deepseek(self):
        note = {
            "title": "GPT Live 口语实践",
            "description": "第一段原文。\n\n第二段保持原样。",
            "publish_date": "2026-08-14",
            "image_paths": [
                "assets/rednote/a.webp",
                "assets/rednote/b.webp",
            ],
        }

        with patch.object(main, "save_markdown_to_oss") as save_markdown:
            filename = main.save_rednote_post(
                note,
                "https://xhslink.cn/o/example",
            )

        self.assertEqual(filename, "GPT Live 口语实践.md")
        markdown, saved_filename = save_markdown.call_args.args
        self.assertEqual(saved_filename, filename)
        self.assertIn("## 原文\n\n第一段原文。\n\n第二段保持原样。", markdown)
        self.assertIn("![小红书图片 1](assets/rednote/a.webp)", markdown)
        self.assertNotIn("SCQA", markdown)
        self.assertNotIn("核心脉络", markdown)

    def test_removes_trailing_rednote_topic_tags_from_original_text(self):
        note = {"title": "标题", "description": "正文里有 #技术 讨论。\n一起加油。\n#失业后的状态 [话题]# #裁员 [话题]#",
                "publish_date": "2026-09-30", "image_paths": []}
        with patch.object(main, "save_markdown_to_oss") as save:
            main.save_rednote_post(note, "https://xhslink.cn/o/example")
        markdown = save.call_args.args[0]
        self.assertIn("正文里有 #技术 讨论。\n一起加油。", markdown)
        self.assertNotIn("[话题]", markdown)

    def test_rednote_image_post_skips_deepseek_pipeline(self):
        note = {
            "title": "图文标题",
            "description": "原始正文",
            "publish_date": "2026-08-14",
            "image_paths": ["assets/rednote/a.webp"],
        }

        with (
            patch.object(main, "download_audio", return_value=(None, "", "", 0)),
            patch.object(main, "fetch_rednote_post", return_value=note),
            patch.object(main, "save_rednote_post", return_value="图文标题.md") as save_rednote,
            patch.object(main, "generate_and_save_markdown") as generate_markdown,
        ):
            with patch.object(main.threading, "Thread") as thread:
                response = main.process_podcast_endpoint({"url": "https://xhslink.cn/o/example"})

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["filename"], "图文标题.md")
        thread.assert_not_called()
        save_rednote.assert_called_once_with(
            note,
            "https://xhslink.cn/o/example",
        )
        generate_markdown.assert_not_called()

    def test_rednote_failure_is_not_reported_as_accepted(self):
        with (
            patch.object(main, "download_audio", return_value=(None, "", "", 0)),
            patch.object(main, "fetch_rednote_post", return_value={}),
            patch.object(main, "fetch_webpage_text", return_value=("", "")),
        ):
            response = main.process_podcast_endpoint({"url": "https://xhslink.cn/o/example"})
        self.assertEqual(response["status"], "error")

    def test_rednote_login_redirect_uses_ytdlp_image_metadata(self):
        class FakeYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, url, download):
                self_url = url
                self.assert_download = download
                return {
                    "id": "6a9fa35300000000250372ae",
                    "title": "被裁满一个月",
                    "description": "投出数百份简历",
                    "thumbnails": [{"url": "https://img.example/1.jpg"}],
                }

        response = types.SimpleNamespace(
            url="https://www.xiaohongshu.com/login?redirectPath=...",
            raise_for_status=lambda: None,
        )
        with (
            patch.object(main.requests, "get", return_value=response),
            patch.object(main, "_download_site_options", return_value=({}, "/tmp/rednote.txt")),
            patch.object(main.yt_dlp, "YoutubeDL", FakeYDL),
            patch.object(main, "_compress_and_upload_rednote_image", return_value=("assets/rednote/1.webp", b"webp")),
            patch.object(main, "_rednote_image_fingerprint", return_value=1),
            patch.object(main, "get_oss_bucket"),
            patch.object(main, "_ocr_rednote_image", return_value="图片文字"),
        ):
            note = main.fetch_rednote_post("https://xhslink.cn/o/vK1nRIGPP1")

        self.assertEqual(note["title"], "被裁满一个月")
        self.assertEqual(note["image_paths"], ["assets/rednote/1.webp"])
        self.assertEqual(note["image_ocr"], ["图片文字"])

    def test_rednote_placeholder_title_uses_description(self):
        class FakeYDL:
            def __init__(self, options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, url, download):
                return {
                    "id": "6a9fa35300000000250372ae",
                    "title": "XiaoHongShu video #6a9fa35300000000250372ae",
                    "description": "被裁满一个月。投出数百份简历\n正文",
                    "thumbnails": [],
                }

        with (
            patch.object(main, "_download_site_options", return_value=({}, None)),
            patch.object(main.yt_dlp, "YoutubeDL", FakeYDL),
        ):
            note = main._fetch_rednote_post_with_ytdlp("https://xhslink.cn/o/example")

        self.assertEqual(note["title"], "被裁满一个月。投出数百份简历")

    def test_rednote_image_uses_https_and_site_referer(self):
        response = types.SimpleNamespace(
            content=b"image",
            raise_for_status=lambda: (_ for _ in ()).throw(ValueError("stop")),
        )
        with patch.object(main.requests, "get", return_value=response) as get:
            with self.assertRaises(ValueError):
                main._compress_and_upload_rednote_image(
                    "http://sns-webpic-qc.xhscdn.com/image", "https://xhslink.cn/o/example"
                )
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://sns-webpic-qc.xhscdn.com/image")
        self.assertEqual(kwargs["headers"]["Referer"], "https://www.xiaohongshu.com/")

    def test_parses_httponly_netscape_cookie(self):
        cookies = (
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.xiaohongshu.com\tTRUE\t/\tTRUE\t0\tweb_session\tabc123\n"
            ".xiaohongshu.com\tTRUE\t/\tFALSE\t0\ta1\tvalue1"
        )

        self.assertEqual(
            main._parse_cookie_header(cookies),
            "web_session=abc123; a1=value1",
        )


if __name__ == "__main__":
    unittest.main()

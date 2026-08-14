import os
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

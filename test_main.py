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


if __name__ == "__main__":
    unittest.main()

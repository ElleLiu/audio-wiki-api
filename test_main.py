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

        def get(self, _path):
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

    def test_recognizes_rednote_overseas_domain(self):
        self.assertTrue(main._is_rednote_url(
            "https://www.rednote.com/discovery/item/abc123"
        ))

    def test_preserves_cookie_across_trusted_rednote_redirect(self):
        redirect = types.SimpleNamespace(
            status_code=302,
            headers={"Location": "https://www.rednote.com/discovery/item/abc123"},
            url="https://www.xiaohongshu.com/discovery/item/abc123",
            raise_for_status=lambda: None,
        )
        final = types.SimpleNamespace(
            status_code=200,
            headers={},
            url="https://www.rednote.com/discovery/item/abc123",
            text="note",
            raise_for_status=lambda: None,
        )
        with (
            patch.object(main, "REDNOTE_COOKIES", "web_session=test-session"),
            patch.object(main.requests, "get", side_effect=[redirect, final]) as get,
        ):
            response = main._get_rednote_page(
                "https://www.xiaohongshu.com/discovery/item/abc123"
            )

        self.assertIs(response, final)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            get.call_args_list[1].kwargs["headers"]["Cookie"],
            "web_session=test-session",
        )
        self.assertFalse(get.call_args_list[1].kwargs["allow_redirects"])

    def test_keeps_canonical_xiaohongshu_url_before_rednote_login(self):
        canonical_url = (
            "https://www.xiaohongshu.com/discovery/item/abc123"
            "?xsec_token=token-value"
        )
        responses = [
            types.SimpleNamespace(
                status_code=302,
                headers={"Location": canonical_url},
                url="https://xhslink.cn/o/example",
                raise_for_status=lambda: None,
            ),
            types.SimpleNamespace(
                status_code=302,
                headers={"Location": "https://www.rednote.com/discovery/item/abc123"},
                url=canonical_url,
                raise_for_status=lambda: None,
            ),
            types.SimpleNamespace(
                status_code=302,
                headers={"Location": "https://www.rednote.com/login"},
                url="https://www.rednote.com/discovery/item/abc123",
                raise_for_status=lambda: None,
            ),
            types.SimpleNamespace(
                status_code=200,
                headers={},
                url="https://www.rednote.com/login",
                text="login",
                raise_for_status=lambda: None,
            ),
        ]
        with patch.object(main.requests, "get", side_effect=responses):
            response = main._get_rednote_page("https://xhslink.cn/o/example")

        self.assertEqual(response.rednote_canonical_xhs_url, canonical_url)

    def test_resolves_rednote_video_to_canonical_xiaohongshu_url(self):
        canonical_url = (
            "https://www.xiaohongshu.com/discovery/item/abc123"
            "?xsec_token=token-value"
        )
        response = types.SimpleNamespace(
            rednote_canonical_xhs_url=canonical_url,
        )
        with patch.object(main, "_get_rednote_page", return_value=response):
            resolved = main._resolve_rednote_media_url(
                "https://xhslink.cn/o/example"
            )

        self.assertEqual(resolved, canonical_url)

    def test_download_audio_uses_canonical_rednote_url(self):
        canonical_url = "https://www.xiaohongshu.com/discovery/item/abc123"
        extracted_urls = []

        class FakeYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, url, download):
                extracted_urls.append(url)
                return {
                    "id": "abc123",
                    "title": "视频标题",
                    "upload_date": "20261028",
                    "duration": 12,
                }

            def prepare_filename(self, info):
                return "/tmp/downloads/video.webm"

        with (
            patch.object(main, "_resolve_rednote_media_url", return_value=canonical_url),
            patch.object(main, "_download_site_options", return_value=({}, None)),
            patch.object(main.yt_dlp, "YoutubeDL", FakeYDL),
        ):
            result = main.download_audio("https://xhslink.cn/o/example")

        self.assertEqual(extracted_urls, [canonical_url])
        self.assertEqual(result[0], "/tmp/downloads/video.mp3")

    def test_login_fallback_uses_canonical_xiaohongshu_url(self):
        canonical_url = "https://www.xiaohongshu.com/discovery/item/abc123"
        login = types.SimpleNamespace(
            url="https://www.rednote.com/login",
            rednote_canonical_xhs_url=canonical_url,
        )
        with (
            patch.object(main, "_get_rednote_page", return_value=login),
            patch.object(main, "_fetch_rednote_post_with_ytdlp", return_value={}) as fallback,
        ):
            main.fetch_rednote_post("https://xhslink.cn/o/example")

        fallback.assert_called_once_with(
            canonical_url,
            save_images=False,
            enable_ocr=False,
        )

    def test_rejects_untrusted_rednote_redirect(self):
        redirect = types.SimpleNamespace(
            status_code=302,
            headers={"Location": "https://example.com/login"},
            url="https://www.xiaohongshu.com/discovery/item/abc123",
            raise_for_status=lambda: None,
        )
        with patch.object(main.requests, "get", return_value=redirect):
            with self.assertRaisesRegex(ValueError, "不受信任"):
                main._get_rednote_page(
                    "https://www.xiaohongshu.com/discovery/item/abc123"
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
            result = main._save_rednote_images(note, "https://www.xiaohongshu.com/", save_images=True, enable_ocr=True)
        self.assertEqual(result["image_paths"], ["assets/rednote/a.webp"])
        self.assertEqual(result["image_ocr"], ["（OCR 识别失败）"])

    def test_media_options_control_image_download_upload_and_ocr(self):
        for save_images, enable_ocr in [(False, False), (False, True), (True, False), (True, True)]:
            with self.subTest(save_images=save_images, enable_ocr=enable_ocr):
                note = {"title": "测试", "description": "正文", "image_urls": ["https://img.example/1.jpg"]}
                with (
                    patch.object(main, "_compress_and_upload_rednote_image", return_value=("assets/rednote/a.webp", b"webp")) as prepare,
                    patch.object(main, "_rednote_image_fingerprint", return_value=1),
                    patch.object(main, "get_oss_bucket") as bucket,
                    patch.object(main, "_ocr_rednote_image", return_value="图片文字") as ocr,
                ):
                    result = main._save_rednote_images(note, "https://www.xiaohongshu.com/",
                                                      save_images=save_images, enable_ocr=enable_ocr)
                self.assertEqual(prepare.call_count, int(save_images or enable_ocr))
                self.assertEqual(bucket.call_count, int(save_images))
                self.assertEqual(ocr.call_count, int(enable_ocr))
                self.assertEqual(result["image_paths"], ["assets/rednote/a.webp"] if save_images else [])
                self.assertEqual(result["image_ocr"], ["图片文字"] if enable_ocr else [])

    def test_media_options_pass_from_endpoint_to_fetcher(self):
        for options in [{}, {"save_images": True}, {"enable_ocr": True},
                        {"save_images": True, "enable_ocr": True}]:
            with (
                self.subTest(options=options),
                patch.object(main, "download_audio", return_value=(None, "", "", 0)),
                patch.object(main, "fetch_rednote_post", return_value={"title": "测试"}) as fetch,
                patch.object(main, "save_rednote_post", return_value="测试.md"),
            ):
                response = main.process_podcast_endpoint({"url": "https://xhslink.cn/o/example", **options})
                self.assertEqual(response["status"], "ok")
                fetch.assert_called_once_with("https://xhslink.cn/o/example",
                                              save_images=options.get("save_images", False),
                                              enable_ocr=options.get("enable_ocr", False))

    def test_string_flags_are_rejected_without_processing(self):
        with patch.object(main, "process_in_background") as process:
            response = main.process_podcast_endpoint({"url": "https://xhslink.cn/o/example", "save_images": "false"})
        self.assertEqual(response["status"], "error")
        process.assert_not_called()

    def test_tracked_validation_error_is_recorded_for_polling(self):
        with (
            patch.object(main, "save_task_status", side_effect=lambda task_id, status, **details: {
                "task_id": task_id, "status": status, **details,
            }) as save_status,
            patch.object(main, "process_tracked_task") as process,
        ):
            response = main.process_podcast_endpoint({
                "url": "https://xhslink.cn/o/example",
                "task_id": "12345678-abcd",
                "save_images": "false",
            })

        self.assertEqual(response["status"], "failed")
        self.assertIn("JSON 布尔值", response["message"])
        save_status.assert_called_once_with(
            "12345678-abcd",
            "failed",
            message="save_images 和 enable_ocr 必须是 JSON 布尔值 true/false",
        )
        process.assert_not_called()

    def test_tracked_task_records_success(self):
        with (
            patch.object(main, "save_task_status", side_effect=lambda task_id, status, **details: {
                "task_id": task_id, "status": status, **details,
            }) as save_status,
            patch.object(main, "process_in_background", return_value="测试.md") as process,
        ):
            response = main.process_podcast_endpoint({
                "url": "https://xhslink.cn/o/example",
                "task_id": "12345678-abcd",
                "save_images": True,
                "enable_ocr": True,
            })

        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(response["filename"], "测试.md")
        self.assertEqual(save_status.call_args_list[0].args, ("12345678-abcd", "processing"))
        self.assertEqual(save_status.call_args_list[0].kwargs, {})
        self.assertEqual(save_status.call_args_list[1].args, ("12345678-abcd", "succeeded"))
        process.assert_called_once_with(
            "https://xhslink.cn/o/example",
            "https://xhslink.cn/o/example",
            "未命名内容",
            "",
            save_images=True,
            enable_ocr=True,
        )

    def test_tracked_task_records_failure(self):
        with (
            patch.object(main, "save_task_status", side_effect=lambda task_id, status, **details: {
                "task_id": task_id, "status": status, **details,
            }) as save_status,
            patch.object(main, "process_in_background", return_value=None),
        ):
            response = main.process_podcast_endpoint({
                "url": "https://xhslink.cn/o/example",
                "task_id": "12345678-abcd",
            })

        self.assertEqual(response["status"], "failed")
        self.assertEqual(save_status.call_args_list[-1].args, ("12345678-abcd", "failed"))

    def test_tracked_rednote_failure_records_safe_detail(self):
        with (
            patch.object(main, "process_in_background", return_value=None),
            patch.object(main, "save_task_status", side_effect=lambda task_id, status, **details: {
                "task_id": task_id, "status": status, **details,
            }),
        ):
            main._rednote_failure_detail.set("网页最终进入登录页；yt-dlp 未返回笔记元数据")
            response = main.process_tracked_task(
                "12345678-abcd",
                "https://xhslink.cn/o/example",
                "https://xhslink.cn/o/example",
                "未命名内容",
                "",
            )

        self.assertIn("网页最终进入登录页", response["message"])

    def test_rednote_failure_detail_hides_cookie(self):
        with patch.object(main, "REDNOTE_COOKIES", "secret-cookie-value"):
            main._rednote_failure_detail.set("")
            main._record_rednote_failure(
                "yt-dlp error secret-cookie-value\nnext line"
            )

        detail = main._rednote_failure_detail.get()
        self.assertNotIn("secret-cookie-value", detail)
        self.assertIn("[已隐藏 Cookie]", detail)
        self.assertNotIn("\n", detail)

    def test_invalid_task_id_is_rejected(self):
        for task_id in ("../bad", 12345678):
            with (
                self.subTest(task_id=task_id),
                patch.object(main, "process_tracked_task") as process,
            ):
                response = main.process_podcast_endpoint({
                    "url": "https://xhslink.cn/o/example",
                    "task_id": task_id,
                })

                self.assertEqual(response["status"], "error")
                process.assert_not_called()

    def test_missing_task_status_is_queued(self):
        bucket = types.SimpleNamespace(object_exists=lambda _key: False)
        with patch.object(main, "get_oss_bucket", return_value=bucket):
            response = main.task_status_endpoint("12345678-abcd")

        self.assertEqual(response, {
            "task_id": "12345678-abcd",
            "status": "queued",
        })

    def test_reads_completed_task_status(self):
        payload = b'{"task_id":"12345678-abcd","status":"succeeded","filename":"test.md"}'
        result = types.SimpleNamespace(read=lambda: payload)
        bucket = types.SimpleNamespace(
            object_exists=lambda _key: True,
            get_object=lambda _key: result,
        )
        with patch.object(main, "get_oss_bucket", return_value=bucket):
            response = main.task_status_endpoint("12345678-abcd")

        self.assertEqual(response["status"], "succeeded")
        self.assertEqual(response["filename"], "test.md")

    def test_ocr_only_note_saves_text_without_image_gallery(self):
        note = {"title": "测试", "description": "", "publish_date": "2026-10-01",
                "image_paths": [], "image_ocr": ["", "图片中的文字"]}
        with patch.object(main, "save_markdown_to_oss") as save:
            main.save_rednote_post(note, "https://xhslink.cn/o/example")
        markdown = save.call_args.args[0]
        self.assertIn("### 图片 2\n\n图片中的文字", markdown)
        self.assertNotIn("## 原图", markdown)
        self.assertNotIn("![", markdown)
        self.assertNotIn("### 图片 1", markdown)

    def test_photo_with_no_text_has_no_empty_ocr_section(self):
        markdown = main._append_image_gallery("正文", ["assets/rednote/a.webp"], ["", "  "])
        self.assertIn("## 原图", markdown)
        self.assertNotIn("图片文字（OCR）", markdown)

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
            result = main._save_rednote_images(note, "https://www.xiaohongshu.com/", save_images=True, enable_ocr=True)
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
            result = main._save_rednote_images(note, "https://www.xiaohongshu.com/", save_images=True, enable_ocr=True)
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
            note = main.fetch_rednote_post("https://xhslink.cn/o/vK1nRIGPP1", save_images=True, enable_ocr=True)

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
                    "description": "\n  \n被裁满一个月。投出数百份简历\n正文",
                    "thumbnails": [],
                }

        with (
            patch.object(main, "_download_site_options", return_value=({}, None)),
            patch.object(main.yt_dlp, "YoutubeDL", FakeYDL),
        ):
            note = main._fetch_rednote_post_with_ytdlp("https://xhslink.cn/o/example")

        self.assertEqual(note["title"], "被裁满一个月。投出数百份简历")

    def test_rednote_empty_title_uses_description(self):
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
                    "title": " \u200b📕\n ",
                    "description": "被裁满一个月。投出数百份简历\n正文",
                    "thumbnails": [],
                }

        with (
            patch.object(main, "_download_site_options", return_value=({}, None)),
            patch.object(main.yt_dlp, "YoutubeDL", FakeYDL),
        ):
            note = main._fetch_rednote_post_with_ytdlp("https://xhslink.cn/o/example")

        self.assertEqual(note["title"], "被裁满一个月。投出数百份简历")

    def test_rednote_without_text_uses_unique_note_id_title(self):
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
                    "title": "📕",
                    "description": "✨",
                    "thumbnails": [{"url": "https://img.example/1.jpg"}],
                }

        with (
            patch.object(main, "_download_site_options", return_value=({}, None)),
            patch.object(main.yt_dlp, "YoutubeDL", FakeYDL),
            patch.object(main, "_compress_and_upload_rednote_image", return_value=("assets/rednote/1.webp", b"webp")),
            patch.object(main, "_rednote_image_fingerprint", return_value=1),
            patch.object(main, "get_oss_bucket"),
        ):
            note = main._fetch_rednote_post_with_ytdlp(
                "https://xhslink.cn/o/example", save_images=True
            )

        self.assertEqual(note["title"], "小红书笔记_6a9fa35300000000250372ae")

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

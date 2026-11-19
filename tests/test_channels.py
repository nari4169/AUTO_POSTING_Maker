import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from common.sources import Item
from naver.compose import build_post as build_blog
from reels.make_video import build_srt, narration_from_text, split_chunks, srt_time
from threads.client import ThreadsClient, ThreadsError
from threads.content import MAX_POST_LENGTH, build_post
from threads.oauth_helper import extract_code


# ── Threads 문구 ─────────────────────────────────────────────
def test_threads_body_within_limit_and_keeps_tail():
    item = Item(id="1", title="제목", text="가" * 900, link="https://x.test/p", hashtags=["태그", "둘째"])
    c = build_post(item, use_ai=False)
    assert len(c.body) <= MAX_POST_LENGTH
    assert c.body.endswith("#태그")           # 첫 해시태그만, 꼬리는 보존
    assert "https://x.test" not in c.body     # 링크는 본문이 아니라 댓글
    assert "https://x.test/p" in c.reply and "수수료" in c.reply  # 댓글에 링크 + 대가성 문구


def test_threads_emoji_counts_as_utf8_bytes():
    from threads.content import weighted_len
    assert weighted_len("가나다") == 3
    assert weighted_len("👇") == 4
    item = Item(id="3", title="t", text="😀" * 200, link="https://x.test/p")  # 200 이모지 = 800
    c = build_post(item, use_ai=False)
    assert weighted_len(c.body) <= MAX_POST_LENGTH


def test_threads_no_link_no_reply():
    c = build_post(Item(id="2", title="t", text="안녕"), use_ai=False)
    assert c.reply == "" and c.body == "안녕"


# ── Threads 클라이언트(가짜 세션) ────────────────────────────
class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)


def test_threads_post_text_two_steps(monkeypatch):
    s = FakeSession([FakeResp(200, {"id": "c1"}), FakeResp(200, {"id": "m1"})])
    client = ThreadsClient("u1", "tok", session=s)
    assert client.post("hello") == "m1"
    assert s.calls[0][1].endswith("/u1/threads") and s.calls[1][1].endswith("/u1/threads_publish")
    assert s.calls[1][2]["data"]["creation_id"] == "c1"


def test_threads_token_error_not_retried():
    s = FakeSession([FakeResp(400, {"error": {"code": 190, "message": "expired"}})])
    client = ThreadsClient("u1", "tok", session=s)
    with pytest.raises(ThreadsError):
        client.create_container("x")
    assert len(s.calls) == 1


def test_threads_rate_limit_retried(monkeypatch):
    monkeypatch.setattr("threads.client.time.sleep", lambda *_: None)
    s = FakeSession([FakeResp(429, {"error": {"code": 4, "message": "rate"}}), FakeResp(200, {"id": "c1"})])
    assert ThreadsClient("u1", "tok", session=s).create_container("x") == "c1"
    assert len(s.calls) == 2


def test_extract_code():
    assert extract_code("https://e.com/cb?code=AQBxyz#_") == "AQBxyz"
    assert extract_code("AQBxyz") == "AQBxyz"


# ── Reels 자막 ───────────────────────────────────────────────
def test_narration_strips_urls_and_tags():
    assert narration_from_text("안녕하세요 https://a.b/c 반가워요 #태그") == "안녕하세요 반가워요"


def test_srt_timing_covers_duration():
    chunks = split_chunks("하나 둘 셋 넷 다섯 여섯 일곱 여덟 아홉 열", 6)
    srt = build_srt(chunks, 10.0)
    assert srt.startswith("1\n00:00:00,000 -->")
    assert "00:00:10,000" in srt.splitlines()[-3] or "00:00:10,000" in srt


def test_srt_time_format():
    assert srt_time(3661.5) == "01:01:01,500"


# ── 네이버 글 ────────────────────────────────────────────────
def test_blog_post_adds_disclosure_with_link():
    p = build_blog(Item(id="1", title="제목", text="본문 " * 100, link="https://x.test", hashtags=["a", "b"]), use_ai=False)
    assert p.title == "제목" and "https://x.test" in p.body and "수수료" in p.body and "#a #b" in p.body


# ── 릴스 영상 호스팅 ─────────────────────────────────────────
def test_hosting_command_mode(monkeypatch, tmp_path):
    from reels import hosting
    calls = []

    class P:
        returncode, stdout, stderr = 0, "uploading...\nhttps://cdn.test/v/abc.mp4\n", ""

    def fake_run(args, **kw):
        calls.append(args)
        return P()

    monkeypatch.setenv("REELS_UPLOAD_COMMAND", "upload.sh {file}")
    monkeypatch.setenv("REELS_DELETE_COMMAND", "delete.sh {url}")
    monkeypatch.setattr(hosting.subprocess, "run", fake_run)
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    h = hosting.host_video(f, "command")
    assert h.url == "https://cdn.test/v/abc.mp4" and calls[0][0] == "upload.sh" and calls[0][1] == str(f)
    h.cleanup()
    assert calls[1] == ["delete.sh", "https://cdn.test/v/abc.mp4"]


def test_hosting_command_must_print_url(monkeypatch, tmp_path):
    from reels import hosting

    class P:
        returncode, stdout, stderr = 0, "done\n", ""

    monkeypatch.setenv("REELS_UPLOAD_COMMAND", "upload.sh {file}")
    monkeypatch.setattr(hosting.subprocess, "run", lambda *a, **k: P())
    with pytest.raises(hosting.HostingError):
        hosting.host_video(tmp_path / "a.mp4", "command")


def test_upload_mode_validation(monkeypatch):
    from common.config import ConfigError
    from reels import hosting
    monkeypatch.setenv("REELS_UPLOAD_MODE", "ftp")
    with pytest.raises(ConfigError):
        hosting.upload_mode()
    monkeypatch.delenv("REELS_UPLOAD_MODE")
    assert hosting.upload_mode() == "s3"


# ── 새 채널 예제(텔레그램) ───────────────────────────────────
def test_telegram_example_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("CONTENT_FILE", str(tmp_path / "posts.json"))
    (tmp_path / "posts.json").write_text('[{"id":"a1","title":"제목","text":"본문"}]', encoding="utf-8")
    from examples import telegram_post as tp

    sent = []

    class R:
        text = ""
        def json(self):
            return {"ok": True, "result": {"message_id": 77}}

    monkeypatch.setattr(tp.requests, "post", lambda url, **kw: sent.append((url, kw)) or R())
    assert tp.main(["--count", "1"]) == 0
    assert tp.main(["--count", "1"]) == 0          # 두 번째는 중복 방지로 건너뜀
    assert len(sent) == 1 and sent[0][1]["data"]["text"].startswith("제목")


def test_telegram_api_error_marks_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("CONTENT_FILE", str(tmp_path / "posts.json"))
    (tmp_path / "posts.json").write_text('[{"id":"a2","title":"제목"}]', encoding="utf-8")
    from common.store import PostStore
    from examples import telegram_post as tp

    class R:
        text = ""
        def json(self):
            return {"ok": False, "description": "chat not found"}

    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: R())
    tp.main(["--count", "1"])
    s = PostStore("telegram")
    assert s.get("a2")["status"] == "failed"
    s.close()


# ── 브라우저 대체 실행 ───────────────────────────────────────
class _FakeChromium:
    def __init__(self, fail_default=False):
        self.fail_default, self.calls = fail_default, []

    def launch(self, **kw):
        self.calls.append(kw)
        if self.fail_default and "executable_path" not in kw:
            raise Exception("BrowserType.launch: Executable doesn't exist at /root/.cache/ms-playwright/x")
        return "browser"


class _FakePW:
    def __init__(self, **kw):
        self.chromium = _FakeChromium(**kw)


def test_launch_uses_playwright_default(monkeypatch):
    from naver.browser import launch_browser
    monkeypatch.delenv("NAVER_BROWSER_PATH", raising=False)
    p = _FakePW()
    assert launch_browser(p, headless=False) == "browser" and p.chromium.calls == [{"headless": False}]


def test_launch_falls_back_to_system_chromium(monkeypatch):
    from naver import browser
    monkeypatch.delenv("NAVER_BROWSER_PATH", raising=False)
    monkeypatch.setattr(browser.shutil, "which", lambda n: "/usr/bin/chromium" if n == "chromium" else None)
    p = _FakePW(fail_default=True)
    assert browser.launch_browser(p, headless=True) == "browser"
    assert p.chromium.calls[-1] == {"executable_path": "/usr/bin/chromium", "headless": True}


def test_launch_explicit_path_and_no_browser_message(monkeypatch):
    from naver import browser
    monkeypatch.setenv("NAVER_BROWSER_PATH", "/opt/chrome/chrome")
    p = _FakePW()
    browser.launch_browser(p, headless=True)
    assert p.chromium.calls == [{"executable_path": "/opt/chrome/chrome", "headless": True}]
    monkeypatch.delenv("NAVER_BROWSER_PATH")
    monkeypatch.setattr(browser.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match="chromium"):
        browser.launch_browser(_FakePW(fail_default=True), headless=True)


def test_auth_url_has_client_id_and_alt_domain(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from threads import oauth_helper as oh
    monkeypatch.setenv("THREADS_APP_ID", "123456")
    monkeypatch.setenv("THREADS_REDIRECT_URI", "https://example.com/cb/")
    for url, host in ((oh.build_auth_url(), "threads.com"), (oh.build_auth_url(oh.AUTH_URL_ALT), "threads.net")):
        u = urlparse(url)
        q = parse_qs(u.query)
        assert u.netloc == host and q["client_id"] == ["123456"] and q["redirect_uri"] == ["https://example.com/cb/"]
        assert "threads_content_publish" in q["scope"][0] and q["response_type"] == ["code"]


def test_extract_code_variants_and_helpful_errors():
    from threads.oauth_helper import extract_code
    assert extract_code("https://e.com/cb?code=AQBxyz#_") == "AQBxyz"
    assert extract_code('"https://e.com/cb?code=AQBxyz#_"') == "AQBxyz"      # 따옴표가 붙어 있어도
    assert extract_code("AQBxyz#_") == "AQBxyz"                              # code 값만 붙여 넣어도
    auth = "https://threads.com/oauth/authorize?client_id=1&redirect_uri=x&scope=a&response_type=code"
    with pytest.raises(RuntimeError, match="인증 코드"):
        extract_code(auth)                                                    # auth-url 주소를 잘못 붙여 넣은 경우
    with pytest.raises(RuntimeError, match="거절"):
        extract_code("https://e.com/cb?error=access_denied&error_description=User+denied")
